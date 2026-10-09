use axum::{
    Router,
    body::{Body, to_bytes},
    extract::Request,
    http::{StatusCode, header},
    response::IntoResponse,
    routing::post,
};
use jev_gateway::{
    AppState, EvaluationRequest, JevClient, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, router,
};
use serde_json::{Value, json};
use std::{
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use tokio::sync::Mutex;
use tower::ServiceExt;

const TOKEN: &str = "gateway-test-token-not-a-real-key-123456";
const UPSTREAM_KEY: &str = "upstream-test-key-not-a-real-one";

struct MockUpstream {
    url: String,
    calls: Arc<AtomicUsize>,
    captured: Arc<Mutex<Vec<(String, String, Value)>>>,
    task: tokio::task::JoinHandle<()>,
}
impl Drop for MockUpstream {
    fn drop(&mut self) {
        self.task.abort();
    }
}

async fn upstream(status: StatusCode, body: String, delay: Duration, prefix: &str) -> MockUpstream {
    let calls = Arc::new(AtomicUsize::new(0));
    let captured = Arc::new(Mutex::new(Vec::new()));
    let c = calls.clone();
    let saved = captured.clone();
    let handler = move |request: Request| {
        let c = c.clone();
        let saved = saved.clone();
        let body = body.clone();
        async move {
            c.fetch_add(1, Ordering::SeqCst);
            let path = request.uri().path().to_owned();
            let auth = request
                .headers()
                .get(header::AUTHORIZATION)
                .unwrap()
                .to_str()
                .unwrap()
                .to_owned();
            let bytes = to_bytes(request.into_body(), MAX_REQUEST_BYTES)
                .await
                .unwrap();
            saved
                .lock()
                .await
                .push((path, auth, serde_json::from_slice(&bytes).unwrap()));
            tokio::time::sleep(delay).await;
            (
                status,
                [
                    (header::RETRY_AFTER, "2"),
                    (header::LOCATION, "/do-not-follow"),
                ],
                body,
            )
                .into_response()
        }
    };
    let app = Router::new().route(&format!("{prefix}/v1/systemone"), post(handler));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}{prefix}", listener.local_addr().unwrap());
    let task = tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });
    MockUpstream {
        url,
        calls,
        captured,
        task,
    }
}

fn payload() -> Value {
    json!({
        "model": "jev-latest", "state": {"command": "Turn off the light"},
        "questions": {
            "action": {"type": "choice", "instructions": "Which action?",
                "criteria": {"off": null, "on": null}},
            "compound": {"type": "noul", "instructions": "More than one command?"},
            "urgency": {"type": "score", "instructions": "How urgent?", "criteria": ["low", "high"]}
        }
    })
}
fn answer() -> Value {
    json!({
        "model": "jev-1.13.0", "usage": {"input_tokens": 321, "output_tokens": 42},
        "answers": {
            "action": {"type": "choice", "choice": "off", "probabilities": {"off": 1.0, "on": 0.0}, "confidence": 1.0},
            "compound": {"type": "noul", "noul": 0.01},
            "urgency": {"type": "score", "score": 0.2, "probabilities": {"0": 0.8, "1": 0.2},
                "legend": {"0": "low", "1": "high"}, "confidence": 0.5}
        }
    })
}
fn app(mock: &MockUpstream, timeout: Duration) -> Router {
    let client =
        JevClient::new(UPSTREAM_KEY.into(), &mock.url, "jev-latest".into(), timeout).unwrap();
    router(AppState::new(client, TOKEN).unwrap())
}
fn request(body: String, token: Option<&str>) -> Request {
    let mut builder = Request::builder()
        .method("POST")
        .uri("/v1/systemone")
        .header(header::CONTENT_TYPE, "application/json");
    if let Some(token) = token {
        builder = builder.header(header::AUTHORIZATION, format!("Bearer {token}"));
    }
    builder.body(Body::from(body)).unwrap()
}

#[tokio::test]
async fn unauthorized_calls_never_reach_upstream() {
    let mock = upstream(StatusCode::OK, answer().to_string(), Duration::ZERO, "").await;
    for token in [None, Some("wrong-token")] {
        let result = app(&mock, Duration::from_secs(2))
            .oneshot(request(payload().to_string(), token))
            .await
            .unwrap();
        assert_eq!(result.status(), StatusCode::UNAUTHORIZED);
    }
    assert_eq!(mock.calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn preserves_wire_contract_option_order_prefix_and_separates_credentials() {
    let mock = upstream(
        StatusCode::OK,
        answer().to_string(),
        Duration::ZERO,
        "/proxy",
    )
    .await;
    let result = app(&mock, Duration::from_secs(2))
        .oneshot(request(payload().to_string(), Some(TOKEN)))
        .await
        .unwrap();
    assert_eq!(result.status(), StatusCode::OK);
    let bytes = to_bytes(result.into_body(), MAX_RESPONSE_BYTES)
        .await
        .unwrap();
    assert_eq!(serde_json::from_slice::<Value>(&bytes).unwrap(), answer());
    let captured = mock.captured.lock().await;
    let (path, auth, forwarded) = &captured[0];
    assert_eq!(path, "/proxy/v1/systemone");
    assert_eq!(auth, &format!("Bearer {UPSTREAM_KEY}"));
    assert_eq!(forwarded, &payload());
    let options: Vec<_> = forwarded["questions"]["action"]["criteria"]
        .as_object()
        .unwrap()
        .keys()
        .collect();
    assert_eq!(options, ["off", "on"]);
}

#[tokio::test]
async fn invalid_requests_are_rejected_without_paid_calls() {
    let mock = upstream(StatusCode::OK, answer().to_string(), Duration::ZERO, "").await;
    let mut empty = payload();
    empty["questions"] = json!({});
    let mut model = payload();
    model["model"] = json!("unapproved-model");
    let mut choice = payload();
    choice["questions"]["action"]["criteria"] = json!({"only": null});
    let mut score = payload();
    score["questions"]["urgency"]["criteria"] = json!(["only"]);
    let mut instructions = payload();
    instructions["questions"]["compound"]["instructions"] = Value::Null;
    let mut state = payload();
    state["state"] = json!(42);
    let mut noul = payload();
    noul["questions"]["compound"]["criteria"] = json!({"wrong": "yes"});
    for body in [empty, model, choice, score, instructions, state, noul] {
        let result = app(&mock, Duration::from_secs(2))
            .oneshot(request(body.to_string(), Some(TOKEN)))
            .await
            .unwrap();
        assert_eq!(result.status(), StatusCode::UNPROCESSABLE_ENTITY);
    }
    let malformed = app(&mock, Duration::from_secs(2))
        .oneshot(request("{".into(), Some(TOKEN)))
        .await
        .unwrap();
    assert_eq!(malformed.status(), StatusCode::UNPROCESSABLE_ENTITY);
    assert_eq!(mock.calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn propagates_status_and_retry_after_without_echoing_secrets_or_retrying() {
    for code in [401, 422, 429, 529] {
        let status = StatusCode::from_u16(code).unwrap();
        let mock = upstream(
            status,
            format!("secret: {UPSTREAM_KEY}"),
            Duration::ZERO,
            "",
        )
        .await;
        let result = app(&mock, Duration::from_secs(2))
            .oneshot(request(payload().to_string(), Some(TOKEN)))
            .await
            .unwrap();
        assert_eq!(result.status(), status);
        assert_eq!(result.headers()[header::RETRY_AFTER], "2");
        let bytes = to_bytes(result.into_body(), MAX_RESPONSE_BYTES)
            .await
            .unwrap();
        assert!(!String::from_utf8_lossy(&bytes).contains(UPSTREAM_KEY));
        assert_eq!(mock.calls.load(Ordering::SeqCst), 1);
    }
}

#[tokio::test]
async fn incomplete_mistyped_and_out_of_range_answers_fail_closed() {
    let mut missing = answer();
    missing["answers"].as_object_mut().unwrap().remove("action");
    let mut kind = answer();
    kind["answers"]["compound"]["type"] = json!("choice");
    let mut probability = answer();
    probability["answers"]["compound"]["noul"] = json!(2.0);
    let mut choice = answer();
    choice["answers"]["action"]["choice"] = json!("invented");
    let mut distribution = answer();
    distribution["answers"]["action"]["probabilities"]["off"] = json!(0.1);
    let mut score = answer();
    score["answers"]["urgency"]["score"] = json!(5.0);
    let mut usage = answer();
    usage["usage"]["input_tokens"] = json!(-1);
    let mut legend = answer();
    legend["answers"]["urgency"]["legend"] = Value::Null;
    for body in [
        missing,
        kind,
        probability,
        choice,
        distribution,
        score,
        usage,
        legend,
    ] {
        let mock = upstream(StatusCode::OK, body.to_string(), Duration::ZERO, "").await;
        let result = app(&mock, Duration::from_secs(2))
            .oneshot(request(payload().to_string(), Some(TOKEN)))
            .await
            .unwrap();
        assert_eq!(result.status(), StatusCode::BAD_GATEWAY);
    }
}

#[tokio::test]
async fn never_follows_upstream_redirects() {
    let mock = upstream(
        StatusCode::TEMPORARY_REDIRECT,
        "".into(),
        Duration::ZERO,
        "",
    )
    .await;
    let result = app(&mock, Duration::from_secs(2))
        .oneshot(request(payload().to_string(), Some(TOKEN)))
        .await
        .unwrap();
    assert_eq!(result.status(), StatusCode::BAD_GATEWAY);
    assert_eq!(mock.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn timeout_is_bounded_without_retry() {
    let mock = upstream(
        StatusCode::OK,
        answer().to_string(),
        Duration::from_secs(1),
        "",
    )
    .await;
    let result = app(&mock, Duration::from_millis(100))
        .oneshot(request(payload().to_string(), Some(TOKEN)))
        .await
        .unwrap();
    assert_eq!(result.status(), StatusCode::GATEWAY_TIMEOUT);
    assert_eq!(mock.calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn request_and_response_sizes_are_bounded() {
    let mock = upstream(
        StatusCode::OK,
        "x".repeat(MAX_RESPONSE_BYTES + 1),
        Duration::ZERO,
        "",
    )
    .await;
    let result = app(&mock, Duration::from_secs(2))
        .oneshot(request("x".repeat(MAX_REQUEST_BYTES + 1), Some(TOKEN)))
        .await
        .unwrap();
    assert_eq!(result.status(), StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(mock.calls.load(Ordering::SeqCst), 0);
    let result = app(&mock, Duration::from_secs(2))
        .oneshot(request(payload().to_string(), Some(TOKEN)))
        .await
        .unwrap();
    assert_eq!(result.status(), StatusCode::BAD_GATEWAY);
}

#[test]
fn rejects_insecure_or_credential_bearing_upstream_urls() {
    for url in [
        "http://192.0.2.10",
        "https://user:pass@example.com",
        "https://example.com?key=secret",
        "https://example.com#fragment",
        "ftp://localhost",
    ] {
        assert!(
            JevClient::new(
                UPSTREAM_KEY.into(),
                url,
                "jev-latest".into(),
                Duration::from_secs(2)
            )
            .is_err()
        );
    }
    let client = JevClient::new(
        UPSTREAM_KEY.into(),
        "http://127.0.0.1",
        "jev-latest".into(),
        Duration::from_secs(2),
    )
    .unwrap();
    assert!(AppState::new(client, "short").is_err());
}

#[test]
fn noul_criteria_and_structured_questions_are_supported() {
    let request: EvaluationRequest = serde_json::from_value(json!({
        "state": ["test"], "model": "jev-latest",
        "questions": {
            "flag": {"type": "noul", "instructions": {"question": "Is this true?"},
                "criteria": {"true": ["yes"], "false": {"meaning": "no"}}}
        }
    }))
    .unwrap();
    assert!(request.validate("jev-latest").is_ok());
}
