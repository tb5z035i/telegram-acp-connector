from __future__ import annotations

import hashlib
import json
import re
import secrets
from pathlib import Path


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "agent"


class LocalIdentityStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.identity_file = self.state_dir / "identity.json"

    def get_or_create_instance_id(self) -> str:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.identity_file.exists():
            payload = json.loads(self.identity_file.read_text(encoding="utf-8"))
            instance_id = payload.get("instance_id")
            if isinstance(instance_id, str) and instance_id:
                return instance_id

        instance_id = secrets.token_hex(8)
        self.identity_file.write_text(
            json.dumps({"instance_id": instance_id}, indent=2),
            encoding="utf-8",
        )
        return instance_id


def build_agent_id(
    *,
    hostname: str,
    workdir: Path,
    configured_agent_id: str | None,
    alias: str | None,
    acp_name: str | None,
    instance_id: str,
) -> str:
    leaf = configured_agent_id or alias or acp_name or instance_id
    workdir_hash = hashlib.sha1(str(workdir).encode("utf-8")).hexdigest()[:10]
    return f"{slugify(hostname)}-{workdir_hash}-{slugify(leaf)}"
