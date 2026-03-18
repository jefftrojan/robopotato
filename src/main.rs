mod auth;
mod config;
mod errors;
mod events;
mod routes;
mod state;

use std::{collections::HashSet, sync::Arc};

use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    middleware,
    response::IntoResponse,
    routing::{get, post},
    Json, Router,
};
use tokio::sync::RwLock;
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};

use auth::{middleware::auth_middleware, token::TokenEngine};
use config::Config;
use events::bus::EventBus;
use state::store::StateStore;

pub struct AppState {
    pub config: Config,
    pub token_engine: TokenEngine,
    pub store: StateStore,
    pub event_bus: EventBus,
    pub revoked_agents: RwLock<HashSet<String>>,
}

#[tokio::main]
async fn main() {
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "robopotato=info,tower_http=debug".into()),
        )
        .with(tracing_subscriber::fmt::layer())
        .init();

    let config = Config::from_env();

    #[cfg(feature = "persist")]
    let store = if config.persist {
        use state::persistence::db;
        let pool = db::init(&config.db_path)
            .await
            .expect("failed to open SQLite database");
        let s = state::store::StateStore::with_db(pool);
        s.load_from_db()
            .await
            .expect("failed to load persisted state");
        s
    } else {
        StateStore::new()
    };

    #[cfg(not(feature = "persist"))]
    let store = StateStore::new();

    let state = Arc::new(AppState {
        token_engine: TokenEngine::new(&config.hmac_secret),
        store,
        event_bus: EventBus::new(),
        revoked_agents: RwLock::new(HashSet::new()),
        config,
    });

    // axum 0.8: protected routes first, route_layer with auth, then public routes
    let app = Router::new()
        // Protected — auth middleware applied via route_layer below
        .route(
            "/agents/{id}",
            axum::routing::delete(routes::agents::revoke),
        )
        .route("/state/namespace/{ns}", get(routes::state::list_namespace))
        .route(
            "/state/{key}",
            get(routes::state::get_state)
                .put(routes::state::set_state)
                .delete(routes::state::delete_state),
        )
        .route_layer(middleware::from_fn_with_state(
            state.clone(),
            auth_middleware,
        ))
        // Public — added after route_layer, not protected
        .route("/health", get(health))
        .route("/agents/register", post(routes::agents::register))
        .route("/tokens/verify", post(routes::tokens::verify_token))
        .route("/events", get(ws_handler))
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state.clone());

    let addr = format!("{}:{}", state.config.host, state.config.port);
    let listener = tokio::net::TcpListener::bind(&addr).await.unwrap();
    tracing::info!("robopotato listening on {}", addr);

    axum::serve(listener, app).await.unwrap();
}

async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "ok",
        "service": "robopotato",
        "version": env!("CARGO_PKG_VERSION"),
    }))
}

/// WebSocket handler — streams state change events to subscribers.
async fn ws_handler(ws: WebSocketUpgrade, State(state): State<Arc<AppState>>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| handle_socket(socket, state))
}

async fn handle_socket(mut socket: WebSocket, state: Arc<AppState>) {
    let mut rx = state.event_bus.subscribe();

    loop {
        tokio::select! {
            event = rx.recv() => {
                match event {
                    Ok(evt) => {
                        if let Ok(json) = serde_json::to_string(&evt) {
                            if socket.send(Message::Text(json.into())).await.is_err() {
                                break;
                            }
                        }
                    }
                    Err(_) => break,
                }
            }
            msg = socket.recv() => {
                match msg {
                    Some(Ok(Message::Close(_))) | None => break,
                    _ => {}
                }
            }
        }
    }
}
