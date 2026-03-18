use axum::{
    extract::{Path, State},
    Extension, Json,
};
use std::sync::Arc;

use crate::{
    auth::token::{Capability, TokenClaims},
    errors::AppError,
    events::bus::Event,
    state::{namespace::Namespace, store::WriteRequest},
    AppState,
};

/// GET /state/:key
pub async fn get_state(
    State(state): State<Arc<AppState>>,
    Extension(claims): Extension<TokenClaims>,
    Path(key): Path<String>,
) -> Result<Json<serde_json::Value>, AppError> {
    let ns = Namespace::from_key(&key)
        .ok_or_else(|| AppError::BadRequest("invalid key namespace".into()))?;

    check_read_access(&claims, &ns)?;

    // For agent namespace, non-orchestrators can only read their own
    if let Namespace::Agent(ref owner) = ns {
        if claims.role != crate::auth::token::Role::Orchestrator && owner != &claims.agent_id {
            return Err(AppError::Forbidden("cannot read another agent's namespace".into()));
        }
    }

    let entry = state
        .store
        .get(&key)
        .await
        .ok_or_else(|| AppError::NotFound(format!("key '{}' not found", key)))?;

    Ok(Json(serde_json::json!({
        "key": entry.key,
        "value": entry.value,
        "version": entry.version,
        "owner_agent_id": entry.owner_agent_id,
        "updated_at": entry.updated_at,
    })))
}

/// PUT /state/:key
pub async fn set_state(
    State(state): State<Arc<AppState>>,
    Extension(claims): Extension<TokenClaims>,
    Path(key): Path<String>,
    Json(req): Json<WriteRequest>,
) -> Result<Json<serde_json::Value>, AppError> {
    let ns = Namespace::from_key(&key)
        .ok_or_else(|| AppError::BadRequest("invalid key namespace".into()))?;

    check_write_access(&claims, &ns)?;

    // For agent namespace, only the owner (or orchestrator) can write
    if let Namespace::Agent(ref owner) = ns {
        if claims.role != crate::auth::token::Role::Orchestrator && owner != &claims.agent_id {
            return Err(AppError::Forbidden("cannot write to another agent's namespace".into()));
        }
    }

    let entry = state.store.set(&key, req, &claims.agent_id).await?;

    state.event_bus.publish(Event::StateChanged {
        key: key.clone(),
        version: entry.version,
        agent_id: claims.agent_id.clone(),
    });

    Ok(Json(serde_json::json!({
        "key": entry.key,
        "version": entry.version,
        "updated_at": entry.updated_at,
    })))
}

/// DELETE /state/:key
pub async fn delete_state(
    State(state): State<Arc<AppState>>,
    Extension(claims): Extension<TokenClaims>,
    Path(key): Path<String>,
) -> Result<Json<serde_json::Value>, AppError> {
    let ns = Namespace::from_key(&key)
        .ok_or_else(|| AppError::BadRequest("invalid key namespace".into()))?;

    // Only orchestrator can delete global/shared; agents can delete their own
    match &ns {
        Namespace::Global | Namespace::Shared => {
            if !claims.has_capability(&Capability::StateWriteGlobal) {
                return Err(AppError::Forbidden("only orchestrator can delete global/shared keys".into()));
            }
        }
        Namespace::Agent(owner) => {
            if claims.role != crate::auth::token::Role::Orchestrator && owner != &claims.agent_id {
                return Err(AppError::Forbidden("cannot delete another agent's keys".into()));
            }
        }
    }

    state.store.delete(&key).await?;
    state.event_bus.publish(Event::StateDeleted {
        key: key.clone(),
        agent_id: claims.agent_id.clone(),
    });

    Ok(Json(serde_json::json!({ "deleted": key })))
}

/// GET /state/namespace/:ns — list all keys in a namespace
pub async fn list_namespace(
    State(state): State<Arc<AppState>>,
    Extension(claims): Extension<TokenClaims>,
    Path(ns): Path<String>,
) -> Result<Json<serde_json::Value>, AppError> {
    let prefix = match ns.as_str() {
        "global" => {
            if !claims.has_capability(&Capability::StateReadGlobal) {
                return Err(AppError::Forbidden("no read access to global namespace".into()));
            }
            "global.".to_string()
        }
        "shared" => {
            if !claims.has_capability(&Capability::StateReadShared) {
                return Err(AppError::Forbidden("no read access to shared namespace".into()));
            }
            "shared.".to_string()
        }
        id => {
            // agent namespace — only owner or orchestrator
            if claims.role != crate::auth::token::Role::Orchestrator && id != claims.agent_id {
                return Err(AppError::Forbidden("cannot list another agent's namespace".into()));
            }
            format!("agent.{}.", id)
        }
    };

    let entries = state.store.list_namespace(&prefix).await;
    Ok(Json(serde_json::json!({ "entries": entries })))
}

fn check_read_access(claims: &TokenClaims, ns: &Namespace) -> Result<(), AppError> {
    let cap = ns.read_capability();
    if !claims.has_capability(&cap) {
        return Err(AppError::Forbidden(format!(
            "missing capability: {}",
            cap
        )));
    }
    Ok(())
}

fn check_write_access(claims: &TokenClaims, ns: &Namespace) -> Result<(), AppError> {
    let cap = ns.write_capability();
    if !claims.has_capability(&cap) {
        return Err(AppError::Forbidden(format!(
            "missing capability: {}",
            cap
        )));
    }
    Ok(())
}
