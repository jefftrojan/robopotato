/// SQLite-backed persistence for StateStore.
///
/// Enabled at runtime when `ROBOPOTATO_PERSIST=true`. The store runs purely
/// in memory; this module provides write-through and startup-recovery so state
/// survives process restarts.

#[cfg(feature = "persist")]
pub mod db {
    use sqlx::{sqlite::SqliteConnectOptions, Row, SqlitePool};
    use std::str::FromStr;

    use crate::state::store::StateEntry;

    const CREATE_TABLE: &str = "
        CREATE TABLE IF NOT EXISTS state_entries (
            key             TEXT    PRIMARY KEY NOT NULL,
            value           TEXT    NOT NULL,
            version         INTEGER NOT NULL,
            owner_agent_id  TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        )
    ";

    /// Open (or create) the SQLite database at `path` and run migrations.
    pub async fn init(path: &str) -> anyhow::Result<SqlitePool> {
        let opts = SqliteConnectOptions::from_str(&format!("sqlite:{}", path))?
            .create_if_missing(true)
            .journal_mode(sqlx::sqlite::SqliteJournalMode::Wal)
            .foreign_keys(true);

        let pool = SqlitePool::connect_with(opts).await?;
        sqlx::query(CREATE_TABLE).execute(&pool).await?;
        Ok(pool)
    }

    /// Load all persisted entries on startup.
    pub async fn load_all(pool: &SqlitePool) -> anyhow::Result<Vec<StateEntry>> {
        let rows = sqlx::query(
            "SELECT key, value, version, owner_agent_id, updated_at FROM state_entries",
        )
        .fetch_all(pool)
        .await?;

        let mut entries = Vec::with_capacity(rows.len());
        for row in rows {
            let key: String = row.try_get("key")?;
            let value_str: String = row.try_get("value")?;
            let version: i64 = row.try_get("version")?;
            let owner_agent_id: String = row.try_get("owner_agent_id")?;
            let updated_at_str: String = row.try_get("updated_at")?;

            let value: serde_json::Value = serde_json::from_str(&value_str)?;
            let updated_at = updated_at_str.parse()?;

            entries.push(StateEntry {
                key,
                value,
                version: version as u64,
                owner_agent_id,
                updated_at,
            });
        }
        Ok(entries)
    }

    /// Upsert a single entry (called after every successful in-memory write).
    pub async fn upsert(pool: &SqlitePool, entry: &StateEntry) -> anyhow::Result<()> {
        let value_str = serde_json::to_string(&entry.value)?;
        let updated_at = entry.updated_at.to_rfc3339();
        sqlx::query(
            "INSERT INTO state_entries (key, value, version, owner_agent_id, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5)
             ON CONFLICT(key) DO UPDATE SET
                 value          = excluded.value,
                 version        = excluded.version,
                 owner_agent_id = excluded.owner_agent_id,
                 updated_at     = excluded.updated_at",
        )
        .bind(&entry.key)
        .bind(&value_str)
        .bind(entry.version as i64)
        .bind(&entry.owner_agent_id)
        .bind(&updated_at)
        .execute(pool)
        .await?;
        Ok(())
    }

    /// Delete a single entry (called after every successful in-memory delete).
    pub async fn remove(pool: &SqlitePool, key: &str) -> anyhow::Result<()> {
        sqlx::query("DELETE FROM state_entries WHERE key = ?1")
            .bind(key)
            .execute(pool)
            .await?;
        Ok(())
    }
}
