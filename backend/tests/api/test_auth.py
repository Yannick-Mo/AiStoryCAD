"""Auth flow tests: register/login/401, user enumeration, password change, logout."""

import uuid

from httpx import AsyncClient


async def _register(client: AsyncClient, username: str | None = None, email: str | None = None, password: str = "secret-pass-123"):
    payload = {
        "username": username or f"user_{uuid.uuid4().hex[:8]}",
        "email": email or f"{uuid.uuid4().hex[:10]}@example.com",
        "password": password,
    }
    resp = await client.post("/api/auth/register", json=payload)
    return resp, payload


async def _auth_headers(client: AsyncClient, email: str, password: str) -> dict:
    resp = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


class TestAuthFlow:
    async def test_register_login_me(self, client):
        resp, payload = await _register(client)
        assert resp.status_code == 200
        token = resp.json()["token"]
        assert token

        headers = {"Authorization": f"Bearer {token}"}
        me = await client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == payload["username"]

        bad_login = await client.post(
            "/api/auth/login",
            json={"email": payload["email"], "password": "wrong-password"},
        )
        assert bad_login.status_code == 401

    async def test_no_token_unauthorized(self, client):
        me = await client.get("/api/auth/me")
        assert me.status_code == 401
        garbage = await client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
        assert garbage.status_code == 401

    async def test_register_weak_password_rejected(self, client):
        resp, _ = await _register(client, password="short")
        assert resp.status_code == 422


class TestUserEnumeration:
    async def test_duplicate_email_generic_409(self, client):
        resp, payload = await _register(client)
        assert resp.status_code == 200
        dup = await client.post(
            "/api/auth/register",
            json={
                "username": "another_user",
                "email": payload["email"],
                "password": "secret-pass-123",
            },
        )
        assert dup.status_code == 409
        assert "Email or username already registered" in dup.json()["detail"]
        assert "Email already registered" not in dup.json()["detail"]

    async def test_duplicate_username_generic_409(self, client):
        resp, payload = await _register(client)
        assert resp.status_code == 200
        dup = await client.post(
            "/api/auth/register",
            json={
                "username": payload["username"],
                "email": "different@example.com",
                "password": "secret-pass-123",
            },
        )
        assert dup.status_code == 409
        assert "Email or username already registered" in dup.json()["detail"]
        assert "Username already taken" not in dup.json()["detail"]


class TestPasswordChange:
    async def test_change_password_requires_old(self, client):
        resp, payload = await _register(client)
        headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        no_old = await client.patch("/api/auth/me", json={"password": "new-pass-456"}, headers=headers)
        assert no_old.status_code == 400

    async def test_change_password_wrong_old_rejected(self, client):
        resp, payload = await _register(client)
        headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        wrong_old = await client.patch(
            "/api/auth/me",
            json={"old_password": "not-the-old", "password": "new-pass-456"},
            headers=headers,
        )
        assert wrong_old.status_code == 400

    async def test_change_password_flow(self, client):
        resp, payload = await _register(client)
        headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        ok = await client.patch(
            "/api/auth/me",
            json={"old_password": payload["password"], "password": "new-pass-456"},
            headers=headers,
        )
        assert ok.status_code == 200
        old_login = await client.post(
            "/api/auth/login",
            json={"email": payload["email"], "password": payload["password"]},
        )
        assert old_login.status_code == 401
        new_login = await client.post(
            "/api/auth/login",
            json={"email": payload["email"], "password": "new-pass-456"},
        )
        assert new_login.status_code == 200

    async def test_change_password_weak_rejected(self, client):
        resp, payload = await _register(client)
        headers = {"Authorization": f"Bearer {resp.json()['token']}"}
        weak = await client.patch(
            "/api/auth/me",
            json={"old_password": payload["password"], "password": "tiny"},
            headers=headers,
        )
        assert weak.status_code == 422


class TestLogout:
    async def test_logout_revokes_token(self, client):
        resp, _ = await _register(client)
        token = resp.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
        out = await client.post("/api/auth/logout", headers=headers)
        assert out.status_code == 200
        assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
