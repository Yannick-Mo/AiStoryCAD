"""Usage endpoint scoping: only the caller's own usage is exposed."""

import uuid

from httpx import AsyncClient


class TestUsageScoping:
    async def test_usage_returns_user_scoped_totals(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={
                "username": f"u_{uuid.uuid4().hex[:8]}",
                "email": f"{uuid.uuid4().hex[:10]}@example.com",
                "password": "secret-pass-123",
            },
        )
        headers = {"Authorization": f"Bearer {resp.json()['token']}"}

        usage = await client.get("/api/v2/usage", headers=headers)
        assert usage.status_code == 200
        body = usage.json()
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
            assert key in body

    async def test_session_usage_unknown_session_404(self, client):
        resp = await client.post(
            "/api/auth/register",
            json={
                "username": f"u_{uuid.uuid4().hex[:8]}",
                "email": f"{uuid.uuid4().hex[:10]}@example.com",
                "password": "secret-pass-123",
            },
        )
        headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        unknown = await client.get(f"/api/v2/usage/session/{uuid.uuid4()}", headers=headers)
        assert unknown.status_code == 404