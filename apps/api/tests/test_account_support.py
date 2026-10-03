"""Self-service account, notifications, favourite stations, card lock / lost, support tickets."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from bson.decimal128 import Decimal128

from app.core.exceptions import ResourceNotFoundError
from app.core.security import hash_password
from app.db.mongodb import get_collection
from app.main import app
from app.modules.auth import account
from app.modules.logs.service import record_error
from app.modules.member import realtime as member_realtime
from app.modules.member.service import verify_member_rfid
from app.websocket.manager import manager
from tests.shared_loop import TEST_LOOP

users = get_collection("users")
rfid_cards = get_collection("rfid_cards")
stations = get_collection("charging_stations")
error_logs = get_collection("error_logs")
audit_logs = get_collection("audit_logs")
notifications = get_collection("notifications")
password_resets = get_collection("password_resets")
favorite_stations = get_collection("favorite_stations")
support_tickets = get_collection("support_tickets")

PASSWORD = "secret-pass"


def run(scenario):
    TEST_LOOP.run_until_complete(scenario())


def api() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class Fixture:
    """Seed users / stations / cards and remove everything they produced afterwards."""

    def __init__(self):
        self.user_ids: list[str] = []
        self.station_ids: list[str] = []

    async def user(self, role: str = "USER", **extra) -> dict:
        now = datetime.now(UTC)
        user_id = f"acct-test-{uuid4()}"
        user = {
            "user_id": user_id,
            "name": f"{role.title()} Test",
            "email": f"{user_id}@test.local",
            "password_hash": hash_password(PASSWORD),
            "role": role,
            "balance_vnd": 100_000,
            "is_active": True,
            "created_at": now,
            "updated_at": now,
            **extra,
        }
        await users.insert_one(user)
        self.user_ids.append(user_id)
        return user

    async def station(self, **extra) -> str:
        now = datetime.now(UTC)
        station_id = f"TEST-{uuid4().hex[:8].upper()}"
        await stations.insert_one(
            {
                "station_id": station_id,
                "name": f"Trạm {station_id}",
                "status": "READY",
                "is_online": True,
                "connector_lock": "UNLOCKED",
                "current_session_id": None,
                "created_at": now,
                "updated_at": now,
                **extra,
            }
        )
        self.station_ids.append(station_id)
        return station_id

    async def card(self, user: dict, **extra) -> dict:
        now = datetime.now(UTC)
        card = {
            "card_id": f"CARD-{uuid4().hex[:8].upper()}",
            "uid": f"UID{uuid4().hex[:8].upper()}",
            "user_id": user["user_id"],
            "label": None,
            "is_active": True,
            "pending": False,
            "created_at": now,
            "updated_at": now,
            **extra,
        }
        await rfid_cards.insert_one(card)
        return card

    async def cleanup(self):
        await notifications.delete_many({"user_id": {"$in": self.user_ids}})
        await notifications.delete_many({"data.station_id": {"$in": self.station_ids}})
        await password_resets.delete_many({"user_id": {"$in": self.user_ids}})
        await favorite_stations.delete_many({"user_id": {"$in": self.user_ids}})
        await support_tickets.delete_many({"user_id": {"$in": self.user_ids}})
        await audit_logs.delete_many({"actor_user_id": {"$in": self.user_ids}})
        await error_logs.delete_many({"station_id": {"$in": self.station_ids}})
        await rfid_cards.delete_many({"user_id": {"$in": self.user_ids}})
        await stations.delete_many({"station_id": {"$in": self.station_ids}})
        await users.delete_many({"user_id": {"$in": self.user_ids}})


async def login(http: httpx.AsyncClient, user: dict, password: str = PASSWORD) -> dict:
    response = await http.post("/api/v1/auth/login", json={"email": user["email"], "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def inbox(user_id: str) -> list[dict]:
    return [n async for n in notifications.find({"user_id": user_id}, {"_id": 0}).sort("created_at", 1)]


# --- Profile & passwords ------------------------------------------------------


@pytest.mark.parametrize("role", ["USER", "TECHNICIAN", "ADMIN"])
def test_profile_update_for_every_role(role):
    async def scenario():
        f = Fixture()
        try:
            user = await f.user(role)
            async with api() as http:
                auth = await login(http, user)

                response = await http.patch(
                    "/api/v1/auth/me",
                    headers=auth,
                    json={"name": "  Nguyễn Văn A ", "phone": "0901 234 567"},
                )
                assert response.status_code == 200, response.text
                assert response.json()["name"] == "Nguyễn Văn A"
                assert response.json()["phone"] == "0901 234 567"

                # Email, role and balance are not editable here.
                response = await http.patch(
                    "/api/v1/auth/me",
                    headers=auth,
                    json={"email": "x@y.z", "role": "ADMIN", "balance_vnd": 9},
                )
                assert response.status_code == 200
                assert response.json()["email"] == user["email"]
                assert response.json()["role"] == role

                # null clears the phone; a malformed phone is rejected.
                response = await http.patch("/api/v1/auth/me", headers=auth, json={"phone": None})
                assert response.json()["phone"] is None
                response = await http.patch("/api/v1/auth/me", headers=auth, json={"phone": "abc"})
                assert response.status_code == 422

                assert (await http.get("/api/v1/auth/me", headers=auth)).json()["name"] == "Nguyễn Văn A"
        finally:
            await f.cleanup()

    run(scenario)


def test_change_password_rotates_tokens_and_notifies():
    async def scenario():
        f = Fixture()
        try:
            user = await f.user("TECHNICIAN")
            async with api() as http:
                auth = await login(http, user)
                other_device = await login(http, user)

                response = await http.post(
                    "/api/v1/auth/change-password",
                    headers=auth,
                    json={"current_password": "wrong", "new_password": "new-secret"},
                )
                assert response.status_code == 400
                assert response.json()["detail"] == "Current password is incorrect"

                response = await http.post(
                    "/api/v1/auth/change-password",
                    headers=auth,
                    json={"current_password": PASSWORD, "new_password": PASSWORD},
                )
                assert response.status_code == 400

                response = await http.post(
                    "/api/v1/auth/change-password",
                    headers=auth,
                    json={"current_password": PASSWORD, "new_password": "short"},
                )
                assert response.status_code == 422

                response = await http.post(
                    "/api/v1/auth/change-password",
                    headers=auth,
                    json={"current_password": PASSWORD, "new_password": "new-secret"},
                )
                assert response.status_code == 200, response.text
                fresh = {"Authorization": f"Bearer {response.json()['access_token']}"}

                # Every older token is revoked, the returned one works.
                assert (await http.get("/api/v1/auth/me", headers=auth)).status_code == 401
                assert (await http.get("/api/v1/auth/me", headers=other_device)).status_code == 401
                assert (await http.get("/api/v1/auth/me", headers=fresh)).status_code == 200

                login_old = await http.post(
                    "/api/v1/auth/login", json={"email": user["email"], "password": PASSWORD}
                )
                assert login_old.status_code == 401
                await login(http, user, "new-secret")

            assert [n["type"] for n in await inbox(user["user_id"])] == ["PASSWORD_CHANGED"]
            assert await audit_logs.count_documents(
                {"actor_user_id": user["user_id"], "action": "PASSWORD_CHANGE"}
            ) == 1
        finally:
            await f.cleanup()

    run(scenario)


def test_forgot_password_issues_temporary_password_then_reset():
    async def scenario():
        f = Fixture()
        try:
            member = await f.user()
            admin = await f.user("ADMIN")
            async with api() as http:
                token_before = await login(http, member)

                response = await http.post("/api/v1/auth/forgot-password", json={"email": "nobody@test.local"})
                assert response.status_code == 404

                # Admin accounts are reset by another admin.
                response = await http.post("/api/v1/auth/forgot-password", json={"email": admin["email"]})
                assert response.status_code == 403

                response = await http.post(
                    "/api/v1/auth/forgot-password", json={"email": member["email"].upper()}
                )
                assert response.status_code == 200, response.text
                first = response.json()["temporary_password"]
                assert len(first) == 10

                # Asking again replaces the first temporary password.
                response = await http.post("/api/v1/auth/forgot-password", json={"email": member["email"]})
                temporary = response.json()["temporary_password"]
                assert temporary != first
                stored = await password_resets.find_one({"user_id": member["user_id"], "used_at": None},
                                                        sort=[("created_at", -1)])
                assert temporary not in str(stored)  # only the hash is stored

                # Nothing changed yet: the current password and tokens still work.
                await login(http, member)
                assert (await http.get("/api/v1/auth/me", headers=token_before)).status_code == 200

                for wrong in ("not-it", first):
                    response = await http.post(
                        "/api/v1/auth/reset-password",
                        json={"email": member["email"], "temporary_password": wrong, "new_password": "brand-new"},
                    )
                    assert response.status_code == 400
                    assert response.json()["detail"] == "Invalid or expired temporary password"

                response = await http.post(
                    "/api/v1/auth/reset-password",
                    json={"email": member["email"], "temporary_password": temporary, "new_password": temporary},
                )
                assert response.status_code == 400

                response = await http.post(
                    "/api/v1/auth/reset-password",
                    json={"email": member["email"], "temporary_password": temporary, "new_password": "brand-new"},
                )
                assert response.status_code == 200, response.text
                fresh = {"Authorization": f"Bearer {response.json()['access_token']}"}
                assert (await http.get("/api/v1/auth/me", headers=fresh)).status_code == 200

                # One use only; old tokens and the old password are gone.
                response = await http.post(
                    "/api/v1/auth/reset-password",
                    json={"email": member["email"], "temporary_password": temporary, "new_password": "again-new"},
                )
                assert response.status_code == 400
                assert (await http.get("/api/v1/auth/me", headers=token_before)).status_code == 401
                response = await http.post("/api/v1/auth/login", json={"email": member["email"], "password": PASSWORD})
                assert response.status_code == 401
                await login(http, member, "brand-new")

            assert [n["type"] for n in await inbox(member["user_id"])] == ["PASSWORD_CHANGED"]
            actions = {
                a["action"] async for a in audit_logs.find({"actor_user_id": member["user_id"]})
            }
            assert actions == {"PASSWORD_RESET_REQUEST", "PASSWORD_RESET"}
        finally:
            await f.cleanup()

    run(scenario)


def test_temporary_password_locks_after_too_many_attempts():
    async def scenario():
        f = Fixture()
        try:
            technician = await f.user("TECHNICIAN")
            async with api() as http:
                response = await http.post("/api/v1/auth/forgot-password", json={"email": technician["email"]})
                temporary = response.json()["temporary_password"]

                for _ in range(account.settings.password_reset_max_attempts):
                    response = await http.post(
                        "/api/v1/auth/reset-password",
                        json={"email": technician["email"], "temporary_password": "nope", "new_password": "brand-new"},
                    )
                    assert response.status_code == 400
                # Even the right temporary password no longer works.
                response = await http.post(
                    "/api/v1/auth/reset-password",
                    json={"email": technician["email"], "temporary_password": temporary, "new_password": "brand-new"},
                )
                assert response.status_code == 400
        finally:
            await f.cleanup()

    run(scenario)


# --- Notifications inbox -------------------------------------------------------


def test_notification_inbox_and_isolation():
    async def scenario():
        f = Fixture()
        received = {"member": [], "technician": []}

        class FakeWebSocket:
            def __init__(self, key):
                self.key = key

            async def send_json(self, data):
                received[self.key].append(data)

        try:
            member = await f.user()
            technician = await f.user("TECHNICIAN")
            member_ws, tech_ws = FakeWebSocket("member"), FakeWebSocket("technician")
            for ws, user in ((member_ws, member), (tech_ws, technician)):
                manager.connections.add(ws)
                manager._identities[ws] = {"user_id": user["user_id"], "role": user["role"]}

            try:
                async with api() as http:
                    auth = await login(http, member)
                    tech_auth = await login(http, technician)
                    await http.patch("/api/v1/auth/me", headers=auth, json={"name": "M"})
                    await http.post(
                        "/api/v1/auth/change-password",
                        headers=auth,
                        json={"current_password": PASSWORD, "new_password": "new-secret"},
                    )
                    auth = await login(http, member, "new-secret")
            finally:
                manager.disconnect(member_ws)
                manager.disconnect(tech_ws)

            # Pushed live to the owner only, not to monitor roles.
            assert [m["notification"]["type"] for m in received["member"]] == ["PASSWORD_CHANGED"]
            assert received["technician"] == []

            async with api() as http:
                body = (await http.get("/api/v1/notifications", headers=auth)).json()
                assert body["total"] == 1 and body["unread_count"] == 1
                notification_id = body["items"][0]["notification_id"]

                # Someone else's notification is invisible.
                response = await http.post(
                    f"/api/v1/notifications/{notification_id}/read", headers=tech_auth
                )
                assert response.status_code == 404
                assert (await http.get("/api/v1/notifications", headers=tech_auth)).json()["total"] == 0

                response = await http.post(f"/api/v1/notifications/{notification_id}/read", headers=auth)
                assert response.status_code == 200
                assert response.json()["read_at"] is not None
                response = await http.get("/api/v1/notifications/unread-count", headers=auth)
                assert response.json() == {"unread_count": 0}
                body = (await http.get("/api/v1/notifications?unread_only=true", headers=auth)).json()
                assert body["items"] == []
                response = await http.post("/api/v1/notifications/read-all", headers=auth)
                assert response.json() == {"updated": 0}
        finally:
            await f.cleanup()

    run(scenario)


def test_station_error_notifies_technicians():
    async def scenario():
        f = Fixture()
        try:
            technician = await f.user("TECHNICIAN")
            member = await f.user()
            station_id = await f.station()

            error = await record_error(station_id=station_id, error_type="E_STOP", severity="CRITICAL")

            items = await inbox(technician["user_id"])
            assert [n["type"] for n in items] == ["STATION_ERROR"]
            assert items[0]["data"]["error_id"] == error["error_id"]
            assert station_id in items[0]["title"]
            assert await inbox(member["user_id"]) == []
        finally:
            await f.cleanup()

    run(scenario)


def test_session_end_notifies_member_and_warns_on_low_balance(monkeypatch):
    async def no_command(station_id, payload):
        return True

    monkeypatch.setattr(member_realtime, "publish_station_command", no_command)

    async def scenario():
        f = Fixture()
        try:
            member = await f.user()
            session = {
                "session_id": f"S-{uuid4()}",
                "station_id": "EVCS-01",
                "user_id": member["user_id"],
                "type": "MEMBER",
                "status": "COMPLETED",
                "energy_kwh": Decimal128("1.25"),
                "cost_vnd": 5_000,
                "stop_reason": "MAX_BUDGET",
                "started_at": datetime.now(UTC),
                "ended_at": datetime.now(UTC),
            }
            transaction = {"transaction_id": "T-1", "amount_vnd": 5_000, "balance_after_vnd": 15_000}
            await member_realtime.announce_stopped({"session": session, "transaction": transaction})

            items = await inbox(member["user_id"])
            assert [n["type"] for n in items] == ["CHARGING_COMPLETED", "LOW_BALANCE"]
            assert "1.25 kWh" in items[0]["body"] and "5.000 đ" in items[0]["body"]
            assert "ngân sách" in items[0]["body"]
            assert items[0]["data"]["session_id"] == session["session_id"]

            # Guest sessions have no account: nothing is stored.
            guest = {**session, "session_id": f"S-{uuid4()}", "user_id": None, "type": "GUEST"}
            await member_realtime.announce_stopped({"session": guest, "transaction": None})
            assert await notifications.count_documents({"data.session_id": guest["session_id"]}) == 0
        finally:
            await f.cleanup()

    run(scenario)


# --- Favourite stations ----------------------------------------------------------


def test_favorite_stations():
    async def scenario():
        f = Fixture()
        try:
            member = await f.user()
            technician = await f.user("TECHNICIAN")
            first = await f.station()
            second = await f.station(is_online=False)

            async with api() as http:
                auth = await login(http, member)
                for station_id in (first, second, second):  # pinning twice is fine
                    response = await http.put(f"/api/v1/member/favorite-stations/{station_id}", headers=auth)
                    assert response.status_code == 204

                response = await http.put("/api/v1/member/favorite-stations/NOPE", headers=auth)
                assert response.status_code == 404

                body = (await http.get("/api/v1/member/favorite-stations", headers=auth)).json()
                assert [s["station_id"] for s in body] == [second, first]  # newest first
                assert body[0]["availability"] == "OFFLINE"
                assert body[1]["availability"] == "AVAILABLE"

                response = await http.delete(f"/api/v1/member/favorite-stations/{second}", headers=auth)
                assert response.status_code == 204
                body = (await http.get("/api/v1/member/favorite-stations", headers=auth)).json()
                assert [s["station_id"] for s in body] == [first]

                # Members only.
                tech_auth = await login(http, technician)
                response = await http.get("/api/v1/member/favorite-stations", headers=tech_auth)
                assert response.status_code == 403
        finally:
            await f.cleanup()

    run(scenario)


# --- Lock / report lost RFID cards -----------------------------------------------


def test_member_locks_unlocks_and_reports_card_lost():
    async def scenario():
        f = Fixture()
        try:
            member = await f.user()
            other = await f.user()
            admin = await f.user("ADMIN")
            card = await f.card(member, label="Xe nhà")

            async with api() as http:
                auth = await login(http, member)
                other_auth = await login(http, other)
                admin_auth = await login(http, admin)

                body = (await http.get("/api/v1/member/rfid-cards", headers=auth)).json()
                assert len(body) == 1
                assert body[0]["status"] == "ACTIVE"
                assert body[0]["uid_suffix"] == card["uid"][-4:]
                assert "uid" not in body[0]

                # Not your card.
                response = await http.post(f"/api/v1/member/rfid-cards/{card['card_id']}/lock", headers=other_auth)
                assert response.status_code == 404

                response = await http.post(f"/api/v1/member/rfid-cards/{card['card_id']}/lock", headers=auth)
                assert response.status_code == 200 and response.json()["status"] == "LOCKED"
                with pytest.raises(ResourceNotFoundError):
                    await verify_member_rfid(uid=card["uid"])

                response = await http.post(f"/api/v1/member/rfid-cards/{card['card_id']}/unlock", headers=auth)
                assert response.status_code == 200 and response.json()["status"] == "ACTIVE"
                await verify_member_rfid(uid=card["uid"])

                response = await http.post(
                    f"/api/v1/member/rfid-cards/{card['card_id']}/report-lost", headers=auth
                )
                assert response.status_code == 200 and response.json()["status"] == "LOST"
                with pytest.raises(ResourceNotFoundError):
                    await verify_member_rfid(uid=card["uid"])

                # LOST is final for the member and for the admin.
                response = await http.post(f"/api/v1/member/rfid-cards/{card['card_id']}/unlock", headers=auth)
                assert response.status_code == 400
                response = await http.post(f"/api/v1/member/rfid-cards/{card['card_id']}/lock", headers=auth)
                assert response.status_code == 400
                response = await http.patch(
                    f"/api/v1/admin/rfid-cards/{card['card_id']}",
                    headers=admin_auth,
                    json={"is_active": True},
                )
                assert response.status_code == 400
                response = await http.get(
                    f"/api/v1/admin/rfid-cards?user_id={member['user_id']}", headers=admin_auth
                )
                admin_view = response.json()["items"][0]
                assert admin_view["status"] == "INACTIVE"
                assert admin_view["block_reason"] == "LOST"

            assert [n["type"] for n in await inbox(member["user_id"])] == [
                "CARD_BLOCKED",
                "CARD_UNLOCKED",
                "CARD_BLOCKED",
            ]
            actions = [
                a["action"]
                async for a in audit_logs.find({"actor_user_id": member["user_id"]}).sort("created_at", 1)
            ]
            assert actions == ["CARD_LOCK", "CARD_UNLOCK", "CARD_REPORT_LOST"]
        finally:
            await f.cleanup()

    run(scenario)


def test_card_locked_by_admin_cannot_be_unlocked_by_owner():
    async def scenario():
        f = Fixture()
        try:
            technician = await f.user("TECHNICIAN")
            admin = await f.user("ADMIN")
            card = await f.card(technician)

            async with api() as http:
                auth = await login(http, technician)
                admin_auth = await login(http, admin)

                response = await http.patch(
                    f"/api/v1/admin/rfid-cards/{card['card_id']}",
                    headers=admin_auth,
                    json={"is_active": False},
                )
                assert response.status_code == 200

                body = (await http.get("/api/v1/technician/rfid-cards", headers=auth)).json()
                assert body[0]["status"] == "INACTIVE"
                response = await http.post(f"/api/v1/technician/rfid-cards/{card['card_id']}/unlock", headers=auth)
                assert response.status_code == 400
                response = await http.post(f"/api/v1/technician/rfid-cards/{card['card_id']}/lock", headers=auth)
                assert response.status_code == 400

                # The owner's own lock is cleared when an admin re-activates the card.
                await http.patch(
                    f"/api/v1/admin/rfid-cards/{card['card_id']}", headers=admin_auth, json={"is_active": True}
                )
                await http.post(f"/api/v1/technician/rfid-cards/{card['card_id']}/lock", headers=auth)
                response = await http.patch(
                    f"/api/v1/admin/rfid-cards/{card['card_id']}", headers=admin_auth, json={"is_active": True}
                )
                assert response.json()["block_reason"] is None
                body = (await http.get("/api/v1/technician/rfid-cards", headers=auth)).json()
                assert body[0]["status"] == "ACTIVE"

            assert [n["type"] for n in await inbox(technician["user_id"])] == [
                "CARD_BLOCKED",
                "CARD_UNLOCKED",
                "CARD_BLOCKED",
                "CARD_UNLOCKED",
            ]
        finally:
            await f.cleanup()

    run(scenario)


# --- Support tickets --------------------------------------------------------------


def test_support_ticket_flow():
    async def scenario():
        f = Fixture()
        try:
            member = await f.user(phone="0901234567")
            tech_a = await f.user("TECHNICIAN")
            tech_b = await f.user("TECHNICIAN")
            station_id = await f.station(
                status="ERROR",
                latest_telemetry={"temperature_c": 71.5, "current_a": 0.0},
            )
            await record_error(station_id=station_id, error_type="OVER_TEMPERATURE", severity="CRITICAL")
            await notifications.delete_many({"data.station_id": station_id})

            async with api() as http:
                auth = await login(http, member)
                a_auth = await login(http, tech_a)
                b_auth = await login(http, tech_b)

                response = await http.post(
                    "/api/v1/member/support-tickets",
                    headers=auth,
                    json={"station_id": "NOPE", "category": "CANNOT_START"},
                )
                assert response.status_code == 404

                response = await http.post(
                    "/api/v1/member/support-tickets",
                    headers=auth,
                    json={
                        "station_id": station_id,
                        "category": "CANNOT_START",
                        "description": "Quẹt thẻ không phản hồi",
                    },
                )
                assert response.status_code == 201, response.text
                ticket = response.json()
                ticket_id = ticket["ticket_id"]
                assert ticket["status"] == "OPEN"
                assert ticket["category_label"] == "Không bắt đầu sạc được"
                assert "station_snapshot" not in ticket  # technician-only detail

                # One open request per station.
                response = await http.post(
                    "/api/v1/member/support-tickets",
                    headers=auth,
                    json={"station_id": station_id, "category": "OTHER"},
                )
                assert response.status_code == 409

                # The station state is logged with the ticket and in the audit log.
                detail = (await http.get(f"/api/v1/technician/support-tickets/{ticket_id}", headers=a_auth)).json()
                snapshot = detail["station_snapshot"]
                assert snapshot["status"] == "ERROR"
                assert snapshot["latest_telemetry"]["temperature_c"] == 71.5
                assert [e["error_type"] for e in snapshot["open_errors"]] == ["OVER_TEMPERATURE"]
                assert detail["user_phone"] == "0901234567"
                audit = await audit_logs.find_one(
                    {"actor_user_id": member["user_id"], "action": "SUPPORT_TICKET_CREATE"}
                )
                assert audit["target_id"] == station_id
                assert audit["metadata"]["open_errors"] == ["OVER_TEMPERATURE"]

                # Every technician is notified.
                for tech in (tech_a, tech_b):
                    items = await inbox(tech["user_id"])
                    assert [n["type"] for n in items] == ["SUPPORT_TICKET_CREATED"]
                    assert items[0]["data"]["ticket_id"] == ticket_id

                queue = (await http.get("/api/v1/technician/support-tickets?status=OPEN", headers=b_auth)).json()
                assert ticket_id in [t["ticket_id"] for t in queue["items"]]

                # Members cannot use technician routes.
                response = await http.get("/api/v1/technician/support-tickets", headers=auth)
                assert response.status_code == 403

                # Resolve needs accept first; only one technician can take it.
                response = await http.post(
                    f"/api/v1/technician/support-tickets/{ticket_id}/resolve",
                    headers=a_auth,
                    json={"resolution_note": "x"},
                )
                assert response.status_code == 400
                response = await http.post(f"/api/v1/technician/support-tickets/{ticket_id}/accept", headers=a_auth)
                assert response.status_code == 200 and response.json()["status"] == "IN_PROGRESS"
                response = await http.post(f"/api/v1/technician/support-tickets/{ticket_id}/accept", headers=b_auth)
                assert response.status_code == 409

                # Taken: the member can no longer cancel.
                response = await http.post(f"/api/v1/member/support-tickets/{ticket_id}/cancel", headers=auth)
                assert response.status_code == 400

                mine = (await http.get("/api/v1/technician/support-tickets?scope=mine", headers=b_auth)).json()
                assert mine["total"] == 0

                response = await http.post(
                    f"/api/v1/technician/support-tickets/{ticket_id}/resolve",
                    headers=a_auth,
                    json={"resolution_note": "Đã reset trạm, sạc lại bình thường"},
                )
                assert response.status_code == 200
                assert response.json()["status"] == "RESOLVED"

                body = (await http.get(f"/api/v1/member/support-tickets/{ticket_id}", headers=auth)).json()
                assert body["status"] == "RESOLVED"
                assert body["assigned_name"] == tech_a["name"]
                assert body["resolution_note"] == "Đã reset trạm, sạc lại bình thường"

                # Closed: a new request for the same station is allowed again.
                response = await http.post(
                    "/api/v1/member/support-tickets",
                    headers=auth,
                    json={"station_id": station_id, "category": "OTHER"},
                )
                assert response.status_code == 201
                second_id = response.json()["ticket_id"]
                response = await http.post(f"/api/v1/member/support-tickets/{second_id}/cancel", headers=auth)
                assert response.json()["status"] == "CANCELLED"

                listing = (await http.get("/api/v1/member/support-tickets", headers=auth)).json()
                assert [t["ticket_id"] for t in listing["items"]] == [second_id, ticket_id]

            assert [n["type"] for n in await inbox(member["user_id"])] == [
                "SUPPORT_TICKET_ACCEPTED",
                "SUPPORT_TICKET_RESOLVED",
            ]
        finally:
            await f.cleanup()

    run(scenario)


# --- Admin: temporary passwords and card blocking -----------------------------------


async def admin_login(http: httpx.AsyncClient, user: dict) -> dict:
    response = await http.post("/api/v1/admin/auth/login", json={"email": user["email"], "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_issues_temporary_password_to_another_admin():
    async def scenario():
        f = Fixture()
        try:
            admin = await f.user("ADMIN")
            forgetful = await f.user("ADMIN")
            locked = await f.user("TECHNICIAN", is_active=False)
            member = await f.user()
            async with api() as http:
                auth = await admin_login(http, admin)

                # An admin cannot get one by "forgot password".
                response = await http.post("/api/v1/auth/forgot-password", json={"email": forgetful["email"]})
                assert response.status_code == 403

                response = await http.post(f"/api/v1/admin/users/{admin['user_id']}/temporary-password", headers=auth)
                assert response.status_code == 400  # own account: change password instead
                response = await http.post(f"/api/v1/admin/users/{locked['user_id']}/temporary-password", headers=auth)
                assert response.status_code == 400
                response = await http.post("/api/v1/admin/users/nope/temporary-password", headers=auth)
                assert response.status_code == 404
                member_auth = await login(http, member)
                response = await http.post(
                    f"/api/v1/admin/users/{forgetful['user_id']}/temporary-password", headers=member_auth
                )
                assert response.status_code == 403

                response = await http.post(
                    f"/api/v1/admin/users/{forgetful['user_id']}/temporary-password", headers=auth
                )
                assert response.status_code == 200, response.text
                temporary = response.json()["temporary_password"]

                # The forgetful admin sets a new password with it and is signed in.
                response = await http.post(
                    "/api/v1/auth/reset-password",
                    json={"email": forgetful["email"], "temporary_password": temporary, "new_password": "fresh-admin"},
                )
                assert response.status_code == 200, response.text
                assert response.json()["user"]["role"] == "ADMIN"
                token = {"Authorization": f"Bearer {response.json()['access_token']}"}
                assert (await http.get("/api/v1/admin/auth/me", headers=token)).status_code == 200

            assert [n["type"] for n in await inbox(forgetful["user_id"])] == [
                "TEMP_PASSWORD_ISSUED",
                "PASSWORD_CHANGED",
            ]
            audit = await audit_logs.find_one({"actor_user_id": admin["user_id"], "action": "PASSWORD_TEMP_ISSUE"})
            assert audit["target_id"] == forgetful["user_id"]
        finally:
            await f.cleanup()

    run(scenario)


def test_admin_locks_or_marks_card_lost_without_touching_the_account():
    async def scenario():
        f = Fixture()
        try:
            admin = await f.user("ADMIN")
            member = await f.user()
            lost_card = await f.card(member)
            spare_card = await f.card(member)
            async with api() as http:
                auth = await admin_login(http, admin)
                member_auth = await login(http, member)

                response = await http.post(
                    f"/api/v1/admin/rfid-cards/{lost_card['card_id']}/block", headers=auth, json={"reason": "LOST"}
                )
                assert response.status_code == 200, response.text
                assert response.json()["status"] == "INACTIVE"
                assert response.json()["block_reason"] == "LOST"

                # The account and the other card keep working.
                assert (await http.get("/api/v1/auth/me", headers=member_auth)).json()["is_active"] is True
                await verify_member_rfid(uid=spare_card["uid"])
                with pytest.raises(ResourceNotFoundError):
                    await verify_member_rfid(uid=lost_card["uid"])

                response = await http.patch(
                    f"/api/v1/admin/rfid-cards/{lost_card['card_id']}", headers=auth, json={"is_active": True}
                )
                assert response.status_code == 400

                # LOCKED by the desk: the member cannot unlock it, the desk can.
                response = await http.post(
                    f"/api/v1/admin/rfid-cards/{spare_card['card_id']}/block", headers=auth, json={"reason": "LOCKED"}
                )
                assert response.json()["block_reason"] == "LOCKED"
                response = await http.post(
                    f"/api/v1/member/rfid-cards/{spare_card['card_id']}/unlock", headers=member_auth
                )
                assert response.status_code == 400
                response = await http.patch(
                    f"/api/v1/admin/rfid-cards/{spare_card['card_id']}", headers=auth, json={"is_active": True}
                )
                assert response.json()["status"] == "ACTIVE"
                assert response.json()["block_reason"] is None

                response = await http.post(
                    f"/api/v1/admin/rfid-cards/{spare_card['card_id']}/block", headers=auth, json={"reason": "BROKEN"}
                )
                assert response.status_code == 422

            items = await inbox(member["user_id"])
            assert [n["type"] for n in items] == ["CARD_BLOCKED", "CARD_BLOCKED", "CARD_UNLOCKED"]
            assert "báo mất" in items[0]["body"]
            assert items[1]["body"].startswith("Quản trị viên đã khóa")
        finally:
            await f.cleanup()

    run(scenario)
