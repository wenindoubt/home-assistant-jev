use jev_gateway::{AppState, JevClient, router};
use std::{env, net::SocketAddr, time::Duration};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let key = env::var("TYPESAFE_API_KEY").map_err(|_| "TYPESAFE_API_KEY is required")?;
    let token = env::var("JEV_GATEWAY_TOKEN").map_err(|_| "JEV_GATEWAY_TOKEN is required")?;
    let base_url =
        env::var("JEV_UPSTREAM_URL").unwrap_or_else(|_| "https://api.typesafe.ai".into());
    let model = env::var("JEV_MODEL").unwrap_or_else(|_| "jev-latest".into());
    let bind: SocketAddr = env::var("JEV_BIND")
        .unwrap_or_else(|_| "127.0.0.1:8093".into())
        .parse()?;
    let client = JevClient::new(key, &base_url, model, Duration::from_secs(20))?;
    let app = router(AppState::new(client, &token)?);
    let listener = tokio::net::TcpListener::bind(bind).await?;
    eprintln!("Jev gateway listening on {}", listener.local_addr()?);
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
