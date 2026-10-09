//! Optional TypeSafe wire-compatible transport. Home Assistant owns all actions.
use std::{collections::BTreeMap, net::IpAddr, sync::Arc, time::Duration};

use axum::{
    Router,
    body::{Body, to_bytes},
    extract::{Request, State},
    http::{StatusCode, header},
    response::Response,
    routing::{get, post},
};
use reqwest::{Client, Url};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;
use tokio::sync::Semaphore;

pub const MAX_REQUEST_BYTES: usize = 512 * 1024;
pub const MAX_RESPONSE_BYTES: usize = 4 * 1024 * 1024;

/// Preserve Choice option order: Jev can be sensitive to it.
#[derive(Clone, Deserialize, Serialize)]
pub struct EvaluationRequest {
    pub state: Value,
    pub model: String,
    pub questions: BTreeMap<String, Value>,
}

fn is_entry(value: &Value) -> bool {
    value.is_string() || value.is_object() || value.is_array()
}

impl EvaluationRequest {
    pub fn validate(&self, model: &str) -> Result<(), GatewayError> {
        if !is_entry(&self.state)
            || self.model != model
            || self.questions.is_empty()
            || self.questions.len() > 1024
        {
            return Err(GatewayError::InvalidRequest);
        }
        for question in self.questions.values() {
            if !question.get("instructions").is_some_and(is_entry) {
                return Err(GatewayError::InvalidRequest);
            }
            let criteria = question.get("criteria");
            let valid = match question.get("type").and_then(Value::as_str) {
                Some("noul") => criteria.is_none_or(|value| {
                    value.as_object().is_some_and(|options| {
                        options.iter().all(|(key, value)| {
                            (key == "true" || key == "false")
                                && (is_entry(value) || value.is_null())
                        })
                    })
                }),
                Some("choice") => criteria.and_then(Value::as_object).is_some_and(|options| {
                    (2..=255).contains(&options.len())
                        && options.values().all(|v| is_entry(v) || v.is_null())
                }),
                Some("score") => criteria.and_then(Value::as_array).is_some_and(|levels| {
                    (2..=10).contains(&levels.len()) && levels.iter().all(is_entry)
                }),
                _ => false,
            };
            if !valid {
                return Err(GatewayError::InvalidRequest);
            }
        }
        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum GatewayError {
    #[error("Invalid gateway configuration")]
    Configuration,
    #[error("Invalid evaluation request")]
    InvalidRequest,
    #[error("Upstream request timed out")]
    Timeout,
    #[error("Upstream connection failed")]
    Connection,
    #[error("Upstream returned an invalid response")]
    InvalidResponse,
}

/// Connection pooling, timeouts, and no redirects or automatic paid retries.
pub struct JevClient {
    http: Client,
    endpoint: Url,
    api_key: String,
    model: String,
}

pub struct UpstreamReply {
    pub status: StatusCode,
    pub retry_after: Option<String>,
    pub body: Vec<u8>,
}

impl JevClient {
    pub fn new(
        api_key: String,
        base_url: &str,
        model: String,
        timeout: Duration,
    ) -> Result<Self, GatewayError> {
        if api_key.is_empty() || api_key.contains(['\r', '\n']) || model.is_empty() {
            return Err(GatewayError::Configuration);
        }
        let mut endpoint = Url::parse(base_url).map_err(|_| GatewayError::Configuration)?;
        let host = endpoint.host_str().ok_or(GatewayError::Configuration)?;
        let loopback = host == "localhost"
            || host
                .trim_matches(['[', ']'])
                .parse::<IpAddr>()
                .is_ok_and(|ip| ip.is_loopback());
        if !(endpoint.scheme() == "https" || endpoint.scheme() == "http" && loopback)
            || !endpoint.username().is_empty()
            || endpoint.password().is_some()
            || endpoint.query().is_some()
            || endpoint.fragment().is_some()
        {
            return Err(GatewayError::Configuration);
        }
        let path = format!("{}/v1/systemone", endpoint.path().trim_end_matches('/'));
        endpoint.set_path(&path);
        let http = Client::builder()
            .timeout(timeout)
            .connect_timeout(Duration::from_secs(5))
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .map_err(|_| GatewayError::Configuration)?;
        Ok(Self {
            http,
            endpoint,
            api_key,
            model,
        })
    }

    pub async fn evaluate(
        &self,
        request: &EvaluationRequest,
    ) -> Result<UpstreamReply, GatewayError> {
        request.validate(&self.model)?;
        let mut response = self
            .http
            .post(self.endpoint.clone())
            .bearer_auth(&self.api_key)
            .json(request)
            .send()
            .await
            .map_err(transport_error)?;
        let status = response.status();
        let retry_after = response
            .headers()
            .get(header::RETRY_AFTER)
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned);
        // Never forward an upstream error body: it could echo credentials or home state.
        if !status.is_success() {
            if status.is_redirection() {
                return Err(GatewayError::InvalidResponse);
            }
            return Ok(UpstreamReply {
                status,
                retry_after,
                body: serde_json::to_vec(&json!({"error": "Jev upstream rejected the request"}))
                    .expect("static JSON serializes"),
            });
        }
        let mut body = Vec::new();
        while let Some(chunk) = response.chunk().await.map_err(transport_error)? {
            if body.len() + chunk.len() > MAX_RESPONSE_BYTES {
                return Err(GatewayError::InvalidResponse);
            }
            body.extend_from_slice(&chunk);
        }
        validate_reply(&body, request)?;
        Ok(UpstreamReply {
            status,
            retry_after,
            body,
        })
    }
}

fn transport_error(error: reqwest::Error) -> GatewayError {
    if error.is_timeout() {
        GatewayError::Timeout
    } else {
        GatewayError::Connection
    }
}

fn validate_reply(body: &[u8], request: &EvaluationRequest) -> Result<(), GatewayError> {
    let invalid = || GatewayError::InvalidResponse;
    let response: Value = serde_json::from_slice(body).map_err(|_| invalid())?;
    let answers = response
        .get("answers")
        .and_then(Value::as_object)
        .ok_or_else(invalid)?;
    if answers.len() != request.questions.len()
        || response.get("model").and_then(Value::as_str).is_none()
        || response
            .pointer("/usage/input_tokens")
            .and_then(Value::as_u64)
            .is_none()
        || response
            .pointer("/usage/output_tokens")
            .and_then(Value::as_u64)
            .is_none()
    {
        return Err(invalid());
    }
    for (key, question) in &request.questions {
        let answer = answers.get(key).ok_or_else(invalid)?;
        if answer.get("type") != question.get("type") {
            return Err(invalid());
        }
        let unit = |value: &Value| value.as_f64().is_some_and(|n| (0.0..=1.0).contains(&n));
        match answer.get("type").and_then(Value::as_str) {
            Some("noul") if answer.get("noul").is_some_and(unit) => {}
            Some("choice") | Some("score") => {
                if !answer.get("confidence").is_some_and(unit) {
                    return Err(invalid());
                }
                let probabilities = answer
                    .get("probabilities")
                    .and_then(Value::as_object)
                    .ok_or_else(invalid)?;
                let sum: f64 = probabilities.values().filter_map(Value::as_f64).sum();
                if probabilities.is_empty()
                    || !probabilities.values().all(unit)
                    || (sum - 1.0).abs() > 0.01
                {
                    return Err(invalid());
                }
                if answer["type"] == "choice" {
                    let criteria = question["criteria"].as_object().ok_or_else(invalid)?;
                    let choice = answer
                        .get("choice")
                        .and_then(Value::as_str)
                        .ok_or_else(invalid)?;
                    if !criteria.contains_key(choice)
                        || probabilities.len() != criteria.len()
                        || !probabilities.keys().all(|k| criteria.contains_key(k))
                    {
                        return Err(invalid());
                    }
                } else {
                    let levels = question["criteria"].as_array().ok_or_else(invalid)?;
                    let score = answer
                        .get("score")
                        .and_then(Value::as_f64)
                        .ok_or_else(invalid)?;
                    if !(0.0..=(levels.len() - 1) as f64).contains(&score)
                        || probabilities.len() != levels.len()
                        || !(0..levels.len()).all(|i| probabilities.contains_key(&i.to_string()))
                        || answer
                            .get("legend")
                            .and_then(Value::as_object)
                            .is_none_or(|legend| {
                                legend.len() != levels.len()
                                    || !(0..levels.len()).all(|i| {
                                        legend.get(&i.to_string()).is_some_and(Value::is_string)
                                    })
                            })
                    {
                        return Err(invalid());
                    }
                }
            }
            _ => return Err(invalid()),
        }
    }
    Ok(())
}

pub struct AppState {
    client: JevClient,
    token_hash: [u8; 32],
    permits: Semaphore,
}

impl AppState {
    pub fn new(client: JevClient, gateway_token: &str) -> Result<Self, GatewayError> {
        if gateway_token.len() < 32 || gateway_token.chars().any(char::is_whitespace) {
            return Err(GatewayError::Configuration);
        }
        Ok(Self {
            client,
            token_hash: Sha256::digest(gateway_token.as_bytes()).into(),
            permits: Semaphore::new(8),
        })
    }
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/health", get(|| async { "ok" }))
        .route("/v1/systemone", post(evaluate))
        .with_state(Arc::new(state))
}

fn error_response(status: StatusCode, message: &str) -> Response {
    (status, axum::Json(json!({"error": message}))).into_response()
}
use axum::response::IntoResponse;

async fn evaluate(State(state): State<Arc<AppState>>, request: Request) -> Response {
    let authorized = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|h| h.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .is_some_and(|token| {
            let hash: [u8; 32] = Sha256::digest(token.as_bytes()).into();
            bool::from(hash.ct_eq(&state.token_hash))
        });
    if !authorized {
        return error_response(StatusCode::UNAUTHORIZED, "Gateway token rejected");
    }
    let Ok(_permit) = state.permits.try_acquire() else {
        return (
            StatusCode::TOO_MANY_REQUESTS,
            [(header::RETRY_AFTER, "1")],
            axum::Json(json!({"error": "Gateway is busy"})),
        )
            .into_response();
    };
    let Ok(body) = to_bytes(request.into_body(), MAX_REQUEST_BYTES).await else {
        return error_response(StatusCode::PAYLOAD_TOO_LARGE, "Request exceeds size limit");
    };
    let Ok(payload) = serde_json::from_slice::<EvaluationRequest>(&body) else {
        return error_response(
            StatusCode::UNPROCESSABLE_ENTITY,
            "Invalid evaluation request",
        );
    };
    match state.client.evaluate(&payload).await {
        Ok(reply) => {
            let mut response = Response::new(Body::from(reply.body));
            *response.status_mut() = reply.status;
            response.headers_mut().insert(
                header::CONTENT_TYPE,
                header::HeaderValue::from_static("application/json"),
            );
            if let Some(value) = reply.retry_after.and_then(|s| s.parse().ok()) {
                response.headers_mut().insert(header::RETRY_AFTER, value);
            }
            response
        }
        Err(GatewayError::InvalidRequest) => error_response(
            StatusCode::UNPROCESSABLE_ENTITY,
            "Invalid evaluation request",
        ),
        Err(GatewayError::Timeout) => {
            error_response(StatusCode::GATEWAY_TIMEOUT, "Jev request timed out")
        }
        Err(_) => error_response(
            StatusCode::BAD_GATEWAY,
            "Jev upstream unavailable or invalid",
        ),
    }
}
