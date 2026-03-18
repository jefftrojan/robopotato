use axum::{
    extract::{Request, State},
    middleware::Next,
    response::Response,
};
use std::sync::Arc;

use crate::{errors::AppError, AppState};

/// Public paths that do not require a Bearer token.
const PUBLIC_PATHS: &[&str] = &[
    "/health",
    "/agents/register",
    "/tokens/verify",
    "/events",
];

/// Applied to ALL routes. Skips auth for public paths, enforces Bearer token
/// for everything else. Injects verified `TokenClaims` as a request extension.
pub async fn auth_middleware(
    State(state): State<Arc<AppState>>,
    mut req: Request,
    next: Next,
) -> Result<Response, AppError> {
    let path = req.uri().path();

    // Skip auth for public endpoints
    if PUBLIC_PATHS.contains(&path) {
        return Ok(next.run(req).await);
    }

    let auth_header = req
        .headers()
        .get("Authorization")
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| AppError::Unauthorized("missing Authorization header".into()))?;

    let token = auth_header
        .strip_prefix("Bearer ")
        .ok_or_else(|| AppError::Unauthorized("expected Bearer token".into()))?;

    let claims = state
        .token_engine
        .verify(token)
        .map_err(|e| AppError::Unauthorized(e.to_string()))?;

    // Check revocation list
    if state.revoked_agents.read().await.contains(&claims.agent_id) {
        return Err(AppError::Unauthorized("agent has been revoked".into()));
    }

    req.extensions_mut().insert(claims);
    Ok(next.run(req).await)
}
