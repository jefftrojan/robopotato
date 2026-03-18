use crate::auth::token::Capability;

/// Namespace of a state key determines access rules.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Namespace {
    /// global.* — readable by all, writable only by orchestrator
    Global,
    /// shared.* — readable by all, writable by workers + orchestrator
    Shared,
    /// agent.<id>.* — readable/writable only by that agent + orchestrator
    Agent(String),
}

impl Namespace {
    /// Parse a key like "global.foo", "shared.bar", "agent.abc123.baz"
    pub fn from_key(key: &str) -> Option<Self> {
        let parts: Vec<&str> = key.splitn(3, '.').collect();
        match parts[0] {
            "global" => Some(Namespace::Global),
            "shared" => Some(Namespace::Shared),
            "agent" if parts.len() >= 2 => Some(Namespace::Agent(parts[1].to_string())),
            _ => None,
        }
    }

    pub fn read_capability(&self) -> Capability {
        match self {
            Namespace::Global => Capability::StateReadGlobal,
            Namespace::Shared => Capability::StateReadShared,
            Namespace::Agent(_) => Capability::StateReadOwn,
        }
    }

    pub fn write_capability(&self) -> Capability {
        match self {
            Namespace::Global => Capability::StateWriteGlobal,
            Namespace::Shared => Capability::StateWriteShared,
            Namespace::Agent(_) => Capability::StateWriteOwn,
        }
    }

    /// For agent namespaces, verify the requesting agent owns this key.
    #[allow(dead_code)]
    pub fn is_owner(&self, agent_id: &str) -> bool {
        match self {
            Namespace::Agent(owner) => owner == agent_id,
            _ => true,
        }
    }
}
