use chrono::{DateTime, Utc};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::fmt;

type HmacSha256 = Hmac<Sha256>;

/// Roles define what an agent is allowed to do at a coarse level.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Role {
    Orchestrator,
    Worker,
    Observer,
}

impl fmt::Display for Role {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Role::Orchestrator => write!(f, "orchestrator"),
            Role::Worker => write!(f, "worker"),
            Role::Observer => write!(f, "observer"),
        }
    }
}

/// Fine-grained capabilities granted to an agent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    StateReadGlobal,
    StateReadShared,
    StateReadOwn,
    StateWriteShared,
    StateWriteOwn,
    StateWriteGlobal,
    AgentList,
    AgentRevoke,
    TokenVerify,
}

impl fmt::Display for Capability {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            Capability::StateReadGlobal => "state:read:global",
            Capability::StateReadShared => "state:read:shared",
            Capability::StateReadOwn => "state:read:own",
            Capability::StateWriteShared => "state:write:shared",
            Capability::StateWriteOwn => "state:write:own",
            Capability::StateWriteGlobal => "state:write:global",
            Capability::AgentList => "agent:list",
            Capability::AgentRevoke => "agent:revoke",
            Capability::TokenVerify => "token:verify",
        };
        write!(f, "{}", s)
    }
}

/// Default capabilities granted per role.
pub fn default_capabilities(role: &Role) -> Vec<Capability> {
    match role {
        Role::Orchestrator => vec![
            Capability::StateReadGlobal,
            Capability::StateReadShared,
            Capability::StateReadOwn,
            Capability::StateWriteGlobal,
            Capability::StateWriteShared,
            Capability::StateWriteOwn,
            Capability::AgentList,
            Capability::AgentRevoke,
            Capability::TokenVerify,
        ],
        Role::Worker => vec![
            Capability::StateReadGlobal,
            Capability::StateReadShared,
            Capability::StateReadOwn,
            Capability::StateWriteShared,
            Capability::StateWriteOwn,
            Capability::TokenVerify,
        ],
        Role::Observer => vec![
            Capability::StateReadGlobal,
            Capability::StateReadShared,
            Capability::StateReadOwn,
        ],
    }
}

/// The claims embedded in a capability token.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenClaims {
    pub agent_id: String,
    pub role: Role,
    pub capabilities: Vec<Capability>,
    pub issued_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub issuer: String,
}

impl TokenClaims {
    pub fn is_expired(&self) -> bool {
        Utc::now() > self.expires_at
    }

    pub fn has_capability(&self, cap: &Capability) -> bool {
        self.capabilities.contains(cap)
    }
}

pub struct TokenEngine {
    secret: Vec<u8>,
}

impl TokenEngine {
    pub fn new(secret: &str) -> Self {
        Self {
            secret: secret.as_bytes().to_vec(),
        }
    }

    /// Sign claims and produce a token string.
    pub fn sign(&self, claims: &TokenClaims) -> anyhow::Result<String> {
        let payload = serde_json::to_string(claims)?;
        let encoded = base64::Engine::encode(
            &base64::engine::general_purpose::URL_SAFE_NO_PAD,
            payload.as_bytes(),
        );
        let sig = self.hmac_sign(&encoded)?;
        Ok(format!("{}.{}", encoded, sig))
    }

    /// Verify a token string and return its claims.
    pub fn verify(&self, token: &str) -> anyhow::Result<TokenClaims> {
        let parts: Vec<&str> = token.splitn(2, '.').collect();
        if parts.len() != 2 {
            anyhow::bail!("malformed token");
        }

        let (encoded, provided_sig) = (parts[0], parts[1]);
        let expected_sig = self.hmac_sign(encoded)?;

        if !constant_time_eq(provided_sig.as_bytes(), expected_sig.as_bytes()) {
            anyhow::bail!("invalid token signature");
        }

        let decoded =
            base64::Engine::decode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, encoded)?;
        let claims: TokenClaims = serde_json::from_slice(&decoded)?;

        if claims.is_expired() {
            anyhow::bail!("token expired");
        }

        Ok(claims)
    }

    fn hmac_sign(&self, data: &str) -> anyhow::Result<String> {
        let mut mac = HmacSha256::new_from_slice(&self.secret)
            .map_err(|e| anyhow::anyhow!("HMAC init error: {}", e))?;
        mac.update(data.as_bytes());
        let result = mac.finalize();
        Ok(hex::encode(result.into_bytes()))
    }
}

/// Constant-time byte comparison to prevent timing attacks.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    a.iter()
        .zip(b.iter())
        .fold(0u8, |acc, (x, y)| acc | (x ^ y))
        == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_claims(role: Role) -> TokenClaims {
        let now = Utc::now();
        TokenClaims {
            agent_id: "agent-test-001".into(),
            role: role.clone(),
            capabilities: default_capabilities(&role),
            issued_at: now,
            expires_at: now + chrono::Duration::hours(1),
            issuer: "orchestrator-root".into(),
        }
    }

    #[test]
    fn sign_and_verify_roundtrip() {
        let engine = TokenEngine::new("super-secret-key");
        let claims = make_claims(Role::Worker);
        let token = engine.sign(&claims).unwrap();
        let verified = engine.verify(&token).unwrap();
        assert_eq!(verified.agent_id, "agent-test-001");
        assert_eq!(verified.role, Role::Worker);
    }

    #[test]
    fn tampered_token_rejected() {
        let engine = TokenEngine::new("super-secret-key");
        let claims = make_claims(Role::Worker);
        let token = engine.sign(&claims).unwrap();

        // Decode the payload, change the agent_id, re-encode WITHOUT re-signing
        let parts: Vec<&str> = token.splitn(2, '.').collect();
        let decoded =
            base64::Engine::decode(&base64::engine::general_purpose::URL_SAFE_NO_PAD, parts[0])
                .unwrap();
        let mut json: serde_json::Value = serde_json::from_slice(&decoded).unwrap();
        json["agent_id"] = serde_json::json!("hacker-999");
        let new_payload = serde_json::to_string(&json).unwrap();
        let new_encoded = base64::Engine::encode(
            &base64::engine::general_purpose::URL_SAFE_NO_PAD,
            new_payload.as_bytes(),
        );
        // Keep the original signature — HMAC should reject this
        let tampered = format!("{}.{}", new_encoded, parts[1]);

        assert!(engine.verify(&tampered).is_err());
    }

    #[test]
    fn wrong_secret_rejected() {
        let engine1 = TokenEngine::new("secret-a");
        let engine2 = TokenEngine::new("secret-b");
        let claims = make_claims(Role::Orchestrator);
        let token = engine1.sign(&claims).unwrap();
        assert!(engine2.verify(&token).is_err());
    }

    #[test]
    fn expired_token_rejected() {
        let engine = TokenEngine::new("secret");
        let now = Utc::now();
        let claims = TokenClaims {
            agent_id: "agent-expired".into(),
            role: Role::Observer,
            capabilities: default_capabilities(&Role::Observer),
            issued_at: now - chrono::Duration::hours(2),
            expires_at: now - chrono::Duration::hours(1),
            issuer: "orchestrator-root".into(),
        };
        let token = engine.sign(&claims).unwrap();
        assert!(engine.verify(&token).is_err());
    }
}
