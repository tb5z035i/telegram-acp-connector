from __future__ import annotations

from pathlib import Path

from telegram_acp_connector.connector.local_identity import LocalIdentityStore, build_agent_id


def test_local_identity_is_persisted(tmp_path: Path) -> None:
    store = LocalIdentityStore(tmp_path / "state")

    first = store.get_or_create_instance_id()
    second = store.get_or_create_instance_id()

    assert first == second
    assert (tmp_path / "state" / "identity.json").exists()


def test_build_agent_id_uses_fallbacks(tmp_path: Path) -> None:
    agent_id = build_agent_id(
        hostname="build-host",
        workdir=tmp_path,
        configured_agent_id=None,
        alias="edge",
        acp_name=None,
        instance_id="deadbeef",
    )

    assert agent_id.startswith("build-host-")
    assert agent_id.endswith("-edge")
