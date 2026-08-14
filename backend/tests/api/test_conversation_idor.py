"""Conversation IDOR regression: user A must not be able to read/write/compress
the conversations of user B, even when holding their own valid project."""

import uuid

from httpx import AsyncClient


async def _register(client: AsyncClient, tag: str):
    resp = await client.post(
        "/api/auth/register",
        json={
            "username": f"{tag}_{uuid.uuid4().hex[:8]}",
            "email": f"{tag}_{uuid.uuid4().hex[:10]}@example.com",
            "password": "secret-pass-123",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


async def _create_project(client: AsyncClient, token: str, title: str) -> str:
    resp = await client.post(
        "/api/projects",
        json={"title": title, "description": ""},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["id"])


async def _create_conversation(client: AsyncClient, token: str, project_id: str) -> str:
    resp = await client.post(
        f"/api/v2/projects/{project_id}/conversations",
        json={"title": "private chat"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["conversation_id"]


async def _make_user(client: AsyncClient, tag: str) -> dict:
    token = await _register(client, tag)
    headers = {"Authorization": f"Bearer {token}"}
    project_id = await _create_project(client, token, f"{tag} project")
    return {"token": token, "headers": headers, "project_id": project_id}


class TestConversationIDOR:
    async def test_user_b_cannot_read_user_a_conversation(self, client):
        a = await _make_user(client, "alice")
        b = await _make_user(client, "bob")
        conv_a = await _create_conversation(client, a["token"], a["project_id"])

        resp = await client.get(
            f"/api/v2/projects/{b['project_id']}/conversations/{conv_a}",
            headers=b["headers"],
        )
        assert resp.status_code == 404

    async def test_user_b_cannot_chat_into_user_a_conversation(self, client):
        a = await _make_user(client, "alice")
        b = await _make_user(client, "bob")
        conv_a = await _create_conversation(client, a["token"], a["project_id"])

        resp = await client.post(
            f"/api/v2/projects/{b['project_id']}/chat",
            json={"message": "hello", "conversation_id": conv_a},
            headers=b["headers"],
        )
        assert resp.status_code == 404

    async def test_user_b_cannot_compress_user_a_conversation(self, client):
        a = await _make_user(client, "alice")
        b = await _make_user(client, "bob")
        conv_a = await _create_conversation(client, a["token"], a["project_id"])

        resp = await client.post(
            f"/api/v2/projects/{b['project_id']}/chat/compress",
            json={"conversation_id": conv_a},
            headers=b["headers"],
        )
        assert resp.status_code == 404

    async def test_user_b_cannot_rename_user_a_conversation(self, client):
        a = await _make_user(client, "alice")
        b = await _make_user(client, "bob")
        conv_a = await _create_conversation(client, a["token"], a["project_id"])

        resp = await client.patch(
            f"/api/v2/projects/{b['project_id']}/conversations/{conv_a}",
            json={"title": "hijacked"},
            headers=b["headers"],
        )
        assert resp.status_code == 404

    async def test_owner_can_still_use_conversation(self, client):
        a = await _make_user(client, "alice")
        conv_a = await _create_conversation(client, a["token"], a["project_id"])

        resp = await client.get(
            f"/api/v2/projects/{a['project_id']}/conversations/{conv_a}",
            headers=a["headers"],
        )
        assert resp.status_code == 200

        renamed = await client.patch(
            f"/api/v2/projects/{a['project_id']}/conversations/{conv_a}",
            json={"title": "my chat"},
            headers=a["headers"],
        )
        assert renamed.status_code == 200

    async def test_user_b_cannot_read_user_a_session_usage(self, client):
        a = await _make_user(client, "alice")
        b = await _make_user(client, "bob")
        conv_a = await _create_conversation(client, a["token"], a["project_id"])

        resp = await client.get(
            f"/api/v2/usage/session/{conv_a}",
            headers=b["headers"],
        )
        assert resp.status_code == 404

        own = await client.get(
            f"/api/v2/usage/session/{conv_a}",
            headers=a["headers"],
        )
        assert own.status_code == 200
