from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from app.core.security import hash_password
from app.db.mongodb import get_collection
from app.main import _websocket_user, app
from tests.shared_loop import TEST_LOOP

users = get_collection("users")

PASSWORD = "Password123!"


async def seed_user(*, is_active: bool = True) -> dict:
    user_id = f"auth-test-{uuid4()}"
    now = datetime.now(UTC)
    user = {
        "user_id": user_id,
        "name": "Auth Test",
        "email": f"{user_id}@test.local",
        "password_hash": hash_password(PASSWORD),
        "role": "USER",
        "balance_vnd": 50_000,
        "is_active": is_active,
        "created_at": now,
        "updated_at": now,
    }
    await users.insert_one(user)
    return user


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_login_me_logout_revokes_token():
    async def scenario():
        user = await seed_user()
        try:
            async with client() as http:
                # Email is matched case-insensitively.
                response = await http.post(
                    "/api/v1/auth/login",
                    json={"email": user["email"].upper(), "password": PASSWORD},
                )
                assert response.status_code == 200
                body = response.json()
                assert body["token_type"] == "bearer"
                assert body["user"]["user_id"] == user["user_id"]
                assert body["user"]["balance_vnd"] == 50_000
                assert "password_hash" not in body["user"]

                auth = {"Authorization": f"Bearer {body['access_token']}"}

                response = await http.get("/api/v1/auth/me", headers=auth)
                assert response.status_code == 200
                assert response.json()["role"] == "USER"

                response = await http.post("/api/v1/auth/logout", headers=auth)
                assert response.status_code == 200

                # The token used before logout no longer works anywhere.
                response = await http.get("/api/v1/auth/me", headers=auth)
                assert response.status_code == 401
                assert response.json()["detail"] == "Token has been revoked"

                response = await http.get("/api/v1/member/wallet", headers=auth)
                assert response.status_code == 401

                # Logging in again issues a working token.
                response = await http.post(
                    "/api/v1/auth/login",
                    json={"email": user["email"], "password": PASSWORD},
                )
                auth = {"Authorization": f"Bearer {response.json()['access_token']}"}
                response = await http.get("/api/v1/auth/me", headers=auth)
                assert response.status_code == 200
        finally:
            await users.delete_many({"user_id": user["user_id"]})

    TEST_LOOP.run_until_complete(scenario())


@pytest.mark.parametrize(
    ("password", "is_active"),
    [("wrong-password", True), (PASSWORD, False)],
)
def test_login_rejects_bad_password_or_inactive_user(password, is_active):
    async def scenario():
        user = await seed_user(is_active=is_active)
        try:
            async with client() as http:
                response = await http.post(
                    "/api/v1/auth/login",
                    json={"email": user["email"], "password": password},
                )
                assert response.status_code == 401
                assert response.json()["detail"] == "Invalid credentials"
        finally:
            await users.delete_many({"user_id": user["user_id"]})

    TEST_LOOP.run_until_complete(scenario())


def test_protected_routes_require_token():
    async def scenario():
        async with client() as http:
            assert (await http.get("/api/v1/auth/me")).status_code == 401
            response = await http.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer not-a-jwt"},
            )
            assert response.status_code == 401

    TEST_LOOP.run_until_complete(scenario())


def test_websocket_rejects_revoked_token():
    async def scenario():
        user = await seed_user()
        try:
            async with client() as http:
                response = await http.post(
                    "/api/v1/auth/login",
                    json={"email": user["email"], "password": PASSWORD},
                )
                token = response.json()["access_token"]

                assert (await _websocket_user(token))["user_id"] == user["user_id"]

                await http.post(
                    "/api/v1/auth/logout",
                    headers={"Authorization": f"Bearer {token}"},
                )

            # /ws closes the connection when the token resolves to no user.
            assert await _websocket_user(token) is None
            assert await _websocket_user("not-a-jwt") is None
        finally:
            await users.delete_many({"user_id": user["user_id"]})

    TEST_LOOP.run_until_complete(scenario())
