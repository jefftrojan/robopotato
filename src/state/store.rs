use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::errors::AppError;

#[cfg(feature = "persist")]
use sqlx::SqlitePool;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateEntry {
    pub key: String,
    pub value: serde_json::Value,
    pub version: u64,
    pub owner_agent_id: String,
    pub updated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WriteRequest {
    pub value: serde_json::Value,
    /// If provided, write only succeeds if current version matches (optimistic lock).
    pub expected_version: Option<u64>,
}

#[derive(Clone)]
pub struct StateStore {
    inner: Arc<RwLock<HashMap<String, StateEntry>>>,
    #[cfg(feature = "persist")]
    db: Option<SqlitePool>,
}

impl StateStore {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
            #[cfg(feature = "persist")]
            db: None,
        }
    }

    #[cfg(feature = "persist")]
    pub fn with_db(pool: SqlitePool) -> Self {
        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
            db: Some(pool),
        }
    }

    /// Populate the in-memory store from persisted rows (called once at startup).
    #[cfg(feature = "persist")]
    pub async fn load_from_db(&self) -> anyhow::Result<()> {
        use super::persistence::db;
        if let Some(pool) = &self.db {
            let entries = db::load_all(pool).await?;
            let count = entries.len();
            let mut store = self.inner.write().await;
            for entry in entries {
                store.insert(entry.key.clone(), entry);
            }
            tracing::info!("loaded {} entries from SQLite", count);
        }
        Ok(())
    }

    pub async fn get(&self, key: &str) -> Option<StateEntry> {
        self.inner.read().await.get(key).cloned()
    }

    pub async fn list_namespace(&self, prefix: &str) -> Vec<StateEntry> {
        self.inner
            .read()
            .await
            .values()
            .filter(|e| e.key.starts_with(prefix))
            .cloned()
            .collect()
    }

    pub async fn set(
        &self,
        key: &str,
        req: WriteRequest,
        agent_id: &str,
    ) -> Result<StateEntry, AppError> {
        let mut store = self.inner.write().await;

        let new_version = if let Some(existing) = store.get(key) {
            // Optimistic locking: if caller provided an expected version, it must match.
            if let Some(ev) = req.expected_version {
                if existing.version != ev {
                    return Err(AppError::Conflict(format!(
                        "version conflict: expected {}, got {}",
                        ev, existing.version
                    )));
                }
            }
            existing.version + 1
        } else {
            1
        };

        let entry = StateEntry {
            key: key.to_string(),
            value: req.value,
            version: new_version,
            owner_agent_id: agent_id.to_string(),
            updated_at: Utc::now(),
        };

        store.insert(key.to_string(), entry.clone());
        drop(store); // release lock before async I/O

        #[cfg(feature = "persist")]
        if let Some(pool) = &self.db {
            use super::persistence::db;
            if let Err(e) = db::upsert(pool, &entry).await {
                tracing::error!("persistence upsert failed for '{}': {}", key, e);
            }
        }

        Ok(entry)
    }

    pub async fn delete(&self, key: &str) -> Result<(), AppError> {
        let mut store = self.inner.write().await;
        if store.remove(key).is_none() {
            return Err(AppError::NotFound(format!("key '{}' not found", key)));
        }
        drop(store);

        #[cfg(feature = "persist")]
        if let Some(pool) = &self.db {
            use super::persistence::db;
            if let Err(e) = db::remove(pool, key).await {
                tracing::error!("persistence delete failed for '{}': {}", key, e);
            }
        }

        Ok(())
    }

    pub async fn snapshot(&self) -> Vec<StateEntry> {
        self.inner.read().await.values().cloned().collect()
    }
}

impl Default for StateStore {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[tokio::test]
    async fn basic_set_get() {
        let store = StateStore::new();
        let req = WriteRequest {
            value: json!("hello"),
            expected_version: None,
        };
        store.set("shared.greeting", req, "agent-1").await.unwrap();
        let entry = store.get("shared.greeting").await.unwrap();
        assert_eq!(entry.value, json!("hello"));
        assert_eq!(entry.version, 1);
    }

    #[tokio::test]
    async fn version_increments() {
        let store = StateStore::new();
        store
            .set(
                "shared.x",
                WriteRequest {
                    value: json!(1),
                    expected_version: None,
                },
                "a",
            )
            .await
            .unwrap();
        store
            .set(
                "shared.x",
                WriteRequest {
                    value: json!(2),
                    expected_version: None,
                },
                "a",
            )
            .await
            .unwrap();
        let entry = store.get("shared.x").await.unwrap();
        assert_eq!(entry.version, 2);
    }

    #[tokio::test]
    async fn optimistic_lock_conflict() {
        let store = StateStore::new();
        store
            .set(
                "shared.y",
                WriteRequest {
                    value: json!(1),
                    expected_version: None,
                },
                "a",
            )
            .await
            .unwrap();
        let result = store
            .set(
                "shared.y",
                WriteRequest {
                    value: json!(2),
                    expected_version: Some(99),
                },
                "a",
            )
            .await;
        assert!(matches!(result, Err(AppError::Conflict(_))));
    }

    #[tokio::test]
    async fn delete_removes_key() {
        let store = StateStore::new();
        store
            .set(
                "global.cfg",
                WriteRequest {
                    value: json!(true),
                    expected_version: None,
                },
                "orch",
            )
            .await
            .unwrap();
        store.delete("global.cfg").await.unwrap();
        assert!(store.get("global.cfg").await.is_none());
    }
}
