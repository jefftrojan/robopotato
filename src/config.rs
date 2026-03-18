use std::env;

#[derive(Debug, Clone)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub hmac_secret: String,
    pub token_ttl_secs: i64,
    pub persist: bool,
    pub db_path: String,
}

fn generate_ephemeral_secret() -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    use std::time::{SystemTime, UNIX_EPOCH};
    let mut h = DefaultHasher::new();
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .hash(&mut h);
    std::process::id().hash(&mut h);
    format!(
        "{:016x}{:016x}",
        h.finish(),
        h.finish().wrapping_mul(0x9e3779b97f4a7c15)
    )
}

impl Config {
    pub fn from_env() -> Self {
        dotenvy::dotenv().ok();

        let hmac_secret = match env::var("ROBOPOTATO_SECRET") {
            Ok(s) if !s.is_empty() => s,
            _ => {
                let secret = generate_ephemeral_secret();
                eprintln!(
                    "\n  ⚠  ROBOPOTATO_SECRET not set — generated an ephemeral secret for this session."
                );
                eprintln!("     Tokens will be invalidated on restart.");
                eprintln!("     For production, set a stable secret:");
                eprintln!("       export ROBOPOTATO_SECRET=$(openssl rand -hex 32)\n");
                secret
            }
        };

        Self {
            host: env::var("ROBOPOTATO_HOST").unwrap_or_else(|_| "127.0.0.1".into()),
            port: env::var("ROBOPOTATO_PORT")
                .unwrap_or_else(|_| "7878".into())
                .parse()
                .unwrap_or(7878),
            hmac_secret,
            token_ttl_secs: env::var("ROBOPOTATO_TOKEN_TTL")
                .unwrap_or_else(|_| "3600".into())
                .parse()
                .unwrap_or(3600),
            persist: env::var("ROBOPOTATO_PERSIST").unwrap_or_else(|_| "false".into()) == "true",
            db_path: env::var("ROBOPOTATO_DB_PATH").unwrap_or_else(|_| "robopotato.db".into()),
        }
    }
}
