"""Usage endpoint scoping in the single-user local tool."""

import uuid

from httpx import AsyncClient


class TestUsageScoping:
    async def test_usage_returns_totals(self, client):
        usage = await client.get("/api/v2/usage")
        assert usage.status_code == 200
        body = usage.json()
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cost"):
            assert key in body

    async def test_session_usage_unknown_session_404(self, client):
        unknown = await client.get(f"/api/v2/usage/session/{uuid.uuid4()}")
        assert unknown.status_code == 404
