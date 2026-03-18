use axum::{extract::State, Json};
use serde::{Deserialize, Serialize};
use std::sync::Arc;

use crate::AppState;

#[derive(Deserialize)]
pub struct VerifyRequest {
    pub token: String,
}

#[derive(Serialize)]
pub struct VerifyResponse {
    pub valid: bool,
    pub agent_id: Option<String>,
    pub role: Option<String>,
    pub capabilities: Option<Vec<String>>,
    pub expires_at: Option<chrono::DateTime<chrono::Utc>>,
    pub reason: Option<String>,
}

/// POST /tokens/verify
/// Allows an agent to verify another agent's token without exposing the secret.
pub async fn verify_token(
    State(state): State<Arc<AppState>>,
    Json(req): Json<VerifyRequest>,
) -> Json<VerifyResponse> {
    match state.token_engine.verify(&req.token) {
        Ok(claims) => {
            // Check if agent has been revoked
            if state.revoked_agents.read().await.contains(&claims.agent_id) {
                return Json(VerifyResponse {
                    valid: false,
                    agent_id: Some(claims.agent_id),
                    role: None,
                    capabilities: None,
                    expires_at: None,
                    reason: Some("agent has been revoked".into()),
                });
            }

            Json(VerifyResponse {
                valid: true,
                agent_id: Some(claims.agent_id),
                role: Some(claims.role.to_string()),
                capabilities: Some(claims.capabilities.iter().map(|c| c.to_string()).collect()),
                expires_at: Some(claims.expires_at),
                reason: None,
            })
        }
        Err(e) => Json(VerifyResponse {
            valid: false,
            agent_id: None,
            role: None,
            capabilities: None,
            expires_at: None,
            reason: Some(e.to_string()),
        }),
    }
}
