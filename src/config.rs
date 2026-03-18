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

impl Config {
    pub fn from_env() -> Self {
        dotenvy::dotenv().ok();

        Self {
            host: env::var("ROBOPOTATO_HOST").unwrap_or_else(|_| "127.0.0.1".into()),
            port: env::var("ROBOPOTATO_PORT")
                .unwrap_or_else(|_| "7878".into())
                .parse()
                .unwrap_or(7878),
            hmac_secret: env::var("ROBOPOTATO_SECRET").expect("ROBOPOTATO_SECRET must be set"),
            token_ttl_secs: env::var("ROBOPOTATO_TOKEN_TTL")
                .unwrap_or_else(|_| "3600".into())
                .parse()
                .unwrap_or(3600),
            persist: env::var("ROBOPOTATO_PERSIST").unwrap_or_else(|_| "false".into()) == "true",
            db_path: env::var("ROBOPOTATO_DB_PATH").unwrap_or_else(|_| "robopotato.db".into()),
        }
    }
}
