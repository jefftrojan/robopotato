use axum::{extract::State, Extension, Json};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use uuid::Uuid;

use crate::{
    auth::token::{default_capabilities, Role, TokenClaims},
    errors::AppError,
    events::bus::Event,
    AppState,
};

#[derive(Debug, Deserialize)]
pub struct RegisterRequest {
    pub name: Option<String>,
    pub role: Role,
}

#[derive(Debug, Serialize)]
pub struct RegisterResponse {
    pub agent_id: String,
    pub token: String,
    pub role: Role,
    pub expires_at: chrono::DateTime<Utc>,
}

/// POST /agents/register
/// Open endpoint — issues a signed capability token for a new agent.
pub async fn register(
    State(state): State<Arc<AppState>>,
    Json(req): Json<RegisterRequest>,
) -> Result<Json<RegisterResponse>, AppError> {
    let agent_id = format!(
        "{}-{}",
        req.name.clone().unwrap_or_else(|| req.role.to_string()),
        Uuid::new_v4().simple()
    );

    let now = Utc::now();
    let expires_at = now + chrono::Duration::seconds(state.config.token_ttl_secs);

    let claims = TokenClaims {
        agent_id: agent_id.clone(),
        role: req.role.clone(),
        capabilities: default_capabilities(&req.role),
        issued_at: now,
        expires_at,
        issuer: "robopotato".into(),
    };

    let token = state
        .token_engine
        .sign(&claims)
        .map_err(|e| AppError::Internal(e.to_string()))?;

    state.event_bus.publish(Event::AgentRegistered {
        agent_id: agent_id.clone(),
        role: req.role.to_string(),
    });

    tracing::info!(agent_id = %agent_id, role = %req.role, "agent registered");

    Ok(Json(RegisterResponse {
        agent_id,
        token,
        role: req.role,
        expires_at,
    }))
}

/// DELETE /agents/:id  (orchestrator only)
pub async fn revoke(
    State(state): State<Arc<AppState>>,
    Extension(claims): Extension<TokenClaims>,
    axum::extract::Path(agent_id): axum::extract::Path<String>,
) -> Result<Json<serde_json::Value>, AppError> {
    use crate::auth::token::Capability;

    if !claims.has_capability(&Capability::AgentRevoke) {
        return Err(AppError::Forbidden(
            "requires agent:revoke capability".into(),
        ));
    }

    state.revoked_agents.write().await.insert(agent_id.clone());
    state.event_bus.publish(Event::AgentRevoked {
        agent_id: agent_id.clone(),
    });

    tracing::info!(agent_id = %agent_id, "agent revoked");
    Ok(Json(serde_json::json!({ "revoked": agent_id })))
}
