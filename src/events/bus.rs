use serde::{Deserialize, Serialize};
use tokio::sync::broadcast;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    StateChanged {
        key: String,
        version: u64,
        agent_id: String,
    },
    StateDeleted {
        key: String,
        agent_id: String,
    },
    AgentRegistered {
        agent_id: String,
        role: String,
    },
    AgentRevoked {
        agent_id: String,
    },
}

#[derive(Clone)]
pub struct EventBus {
    sender: broadcast::Sender<Event>,
}

impl EventBus {
    pub fn new() -> Self {
        let (sender, _) = broadcast::channel(256);
        Self { sender }
    }

    pub fn publish(&self, event: Event) {
        // Ignore send errors — no subscribers is fine
        let _ = self.sender.send(event);
    }

    pub fn subscribe(&self) -> broadcast::Receiver<Event> {
        self.sender.subscribe()
    }
}

impl Default for EventBus {
    fn default() -> Self {
        Self::new()
    }
}
