"""
Thin Python client for the Robopotato API.
Framework-agnostic — just httpx over HTTP.
"""
import httpx
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class AgentIdentity:
    agent_id: str
    token: str
    role: str
    expires_at: str


@dataclass
class StateEntry:
    key: str
    value: Any
    version: int
    owner_agent_id: str
    updated_at: str


class RobopotatoClient:
    def __init__(self, base_url: str = "http://127.0.0.1:7878"):
        self.base_url = base_url
        self.identity: Optional[AgentIdentity] = None
        self._client = httpx.Client(timeout=10.0)

    @property
    def token(self) -> Optional[str]:
        return self.identity.token if self.identity else None

    @property
    def agent_id(self) -> Optional[str]:
        return self.identity.agent_id if self.identity else None

    def _headers(self) -> dict:
        if not self.token:
            raise RuntimeError("Agent not registered — call register() first")
        return {"Authorization": f"Bearer {self.token}"}

    def health(self) -> dict:
        r = self._client.get(f"{self.base_url}/health")
        r.raise_for_status()
        return r.json()

    def register(self, role: str = "worker", name: Optional[str] = None) -> AgentIdentity:
        payload = {"role": role}
        if name:
            payload["name"] = name
        r = self._client.post(f"{self.base_url}/agents/register", json=payload)
        r.raise_for_status()
        data = r.json()
        self.identity = AgentIdentity(
            agent_id=data["agent_id"],
            token=data["token"],
            role=data["role"],
            expires_at=data["expires_at"],
        )
        return self.identity

    def revoke(self, agent_id: str) -> dict:
        r = self._client.delete(
            f"{self.base_url}/agents/{agent_id}",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def get_state(self, key: str) -> StateEntry:
        r = self._client.get(
            f"{self.base_url}/state/{key}",
            headers=self._headers(),
        )
        r.raise_for_status()
        d = r.json()
        return StateEntry(
            key=d["key"],
            value=d["value"],
            version=d["version"],
            owner_agent_id=d["owner_agent_id"],
            updated_at=d["updated_at"],
        )

    def set_state(
        self,
        key: str,
        value: Any,
        expected_version: Optional[int] = None,
    ) -> dict:
        payload: dict = {"value": value}
        if expected_version is not None:
            payload["expected_version"] = expected_version
        r = self._client.put(
            f"{self.base_url}/state/{key}",
            json=payload,
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def delete_state(self, key: str) -> dict:
        r = self._client.delete(
            f"{self.base_url}/state/{key}",
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    def list_namespace(self, ns: str) -> list[StateEntry]:
        r = self._client.get(
            f"{self.base_url}/state/namespace/{ns}",
            headers=self._headers(),
        )
        r.raise_for_status()
        entries = r.json().get("entries", [])
        return [
            StateEntry(
                key=e["key"],
                value=e["value"],
                version=e["version"],
                owner_agent_id=e["owner_agent_id"],
                updated_at=e["updated_at"],
            )
            for e in entries
        ]

    def verify_token(self, token: str) -> dict:
        r = self._client.post(
            f"{self.base_url}/tokens/verify",
            json={"token": token},
        )
        r.raise_for_status()
        return r.json()

    def try_set_state(self, key: str, value: Any, expected_version: Optional[int] = None) -> tuple[bool, str]:
        """Returns (success, reason). Does not raise on auth/conflict errors."""
        try:
            self.set_state(key, value, expected_version)
            return True, "ok"
        except httpx.HTTPStatusError as e:
            try:
                reason = e.response.json().get("error", str(e))
            except Exception:
                reason = str(e)
            return False, reason
