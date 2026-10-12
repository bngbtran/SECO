from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from bson.decimal128 import Decimal128

from app.core.security import hash_password
from app.db.mongodb import get_collection
from app.main import app
from app.modules.admin import stations as admin_stations
from app.modules.guest import realtime as guest_realtime
from app.modules.member import realtime as member_realtime
from app.modules.member.service import start_member_charging, verify_member_rfid
from app.mqtt.handlers import handle_message
from app.websocket.manager import manager
from tests.shared_loop import TEST_LOOP

users = get_collection("users")
rfid_cards = get_collection("rfid_cards")
stations = get_collection("charging_stations")
sessions = get_collection("charging_sessions")
transactions = get_collection("transactions")
payment_intents = get_collection("payment_intents")
maintenance_records = get_collection("maintenance_records")
error_logs = get_collection("error_logs")
audit_logs = get_collection("audit_logs")
price_history = get_collection("price_history")

PASSWORD = "admin-pass"


def run(scenario):
    TEST_LOOP.run_until_complete(scenario())


def api() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def seed_user(role: str = "USER", balance: int = 100_000, **extra) -> dict:
    now = datetime.now(UTC)
    user_id = f"admin-test-{uuid4()}"
    user = {
        "user_id": user_id,
        "name": f"{role.title()} Test",
        "email": f"{user_id}@test.local",
        "password_hash": hash_password(PASSWORD),
        "role": role,
        "balance_vnd": balance,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
        **extra,
    }
    await users.insert_one(user)
    return user


async def seed_station(**extra) -> str:
    now = datetime.now(UTC)
    station_id = f"TEST-{uuid4().hex[:8].upper()}"
    await stations.insert_one(
        {
            "station_id": station_id,
            "name": "Admin Test Station",
            "status": "READY",
            "is_online": True,
            "connector_lock": "UNLOCKED",
            "current_session_id": None,
            "created_at": now,
            "updated_at": now,
            **extra,
        }
    )
    return station_id


async def login(http: httpx.AsyncClient, user: dict) -> dict:
    response = await http.post(
        "/api/v1/admin/auth/login",
        json={"email": user["email"], "password": PASSWORD},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


class Cleanup:
    """Track what a test creates and remove it afterwards."""

    def __init__(self):
        self.user_ids: set[str] = set()
        self.station_ids: set[str] = set()
        self.card_ids: set[str] = set()

    async def __call__(self):
        session_ids = [
            s["session_id"]
            async for s in sessions.find(
                {
                    "$or": [
                        {"station_id": {"$in": list(self.station_ids)}},
                        {"user_id": {"$in": list(self.user_ids)}},
                    ]
                },
                {"session_id": 1},
            )
        ]
        await transactions.delete_many(
            {"$or": [{"session_id": {"$in": session_ids}}, {"user_id": {"$in": list(self.user_ids)}}]}
        )
        await payment_intents.delete_many({"session_id": {"$in": session_ids}})
        await sessions.delete_many({"session_id": {"$in": session_ids}})
        await maintenance_records.delete_many({"station_id": {"$in": list(self.station_ids)}})
        await error_logs.delete_many({"station_id": {"$in": list(self.station_ids)}})
        await audit_logs.delete_many({"actor_user_id": {"$in": list(self.user_ids)}})
        await rfid_cards.delete_many(
            {"$or": [{"card_id": {"$in": list(self.card_ids)}}, {"user_id": {"$in": list(self.user_ids)}}]}
        )
        await stations.delete_many({"station_id": {"$in": list(self.station_ids)}})
        await users.delete_many({"user_id": {"$in": list(self.user_ids)}})


@pytest.fixture
def captured(monkeypatch):
    data = {"commands": [], "broadcasts": []}

    async def fake_command(station_id, payload):
        data["commands"].append(payload)
        return True

    async def fake_broadcast(payload):
        data["broadcasts"].append(payload)

    monkeypatch.setattr(admin_stations, "publish_station_command", fake_command)
    monkeypatch.setattr(guest_realtime, "publish_station_command", fake_command)
    monkeypatch.setattr(member_realtime, "publish_station_command", fake_command)
    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    return data


# UC-A01 ----------------------------------------------------------------------


def test_admin_login_only_for_admins_and_routes_require_admin():
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        member = await seed_user("USER")
        clean.user_ids |= {admin["user_id"], member["user_id"]}
        try:
            async with api() as http:
                response = await http.post(
                    "/api/v1/admin/auth/login",
                    json={"email": member["email"], "password": PASSWORD},
                )
                assert response.status_code == 403

                response = await http.post(
                    "/api/v1/admin/auth/login",
                    json={"email": admin["email"], "password": "wrong"},
                )
                assert response.status_code == 401

                # A member token cannot reach admin routes.
                response = await http.post(
                    "/api/v1/auth/login",
                    json={"email": member["email"], "password": PASSWORD},
                )
                member_auth = {"Authorization": f"Bearer {response.json()['access_token']}"}
                assert (await http.get("/api/v1/admin/users", headers=member_auth)).status_code == 403
                assert (await http.get("/api/v1/admin/users")).status_code == 401

                auth = await login(http, admin)
                assert (await http.get("/api/v1/admin/auth/me", headers=auth)).json()["role"] == "ADMIN"
                assert (await http.post("/api/v1/admin/auth/logout", headers=auth)).status_code == 200
                assert (await http.get("/api/v1/admin/auth/me", headers=auth)).status_code == 401

            actions = [a["action"] async for a in audit_logs.find({"actor_user_id": admin["user_id"]})]
            assert sorted(actions) == ["ADMIN_LOGIN", "ADMIN_LOGOUT"]
        finally:
            await clean()

    run(scenario)


# UC-A02 ----------------------------------------------------------------------


def test_admin_user_crud_and_guards():
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        clean.user_ids.add(admin["user_id"])
        try:
            async with api() as http:
                auth = await login(http, admin)
                email = f"new-{uuid4().hex[:6]}@test.local"

                response = await http.post(
                    "/api/v1/admin/users",
                    headers=auth,
                    json={"name": "Nguyễn Mới", "email": email.upper(), "password": "seco", "role": "USER"},
                )
                assert response.status_code == 201, response.text
                created = response.json()
                clean.user_ids.add(created["user_id"])
                assert created["email"] == email
                assert created["balance_vnd"] == 0
                assert "password_hash" not in created

                response = await http.post(
                    "/api/v1/admin/users",
                    headers=auth,
                    json={"name": "Dup", "email": email, "password": "seco"},
                )
                assert response.status_code == 409

                response = await http.get(f"/api/v1/admin/users?q={email}", headers=auth)
                assert response.json()["total"] == 1

                response = await http.patch(
                    f"/api/v1/admin/users/{created['user_id']}",
                    headers=auth,
                    json={"phone": "0905 000 999", "is_active": False},
                )
                assert response.json()["phone"] == "0905 000 999"
                assert response.json()["is_active"] is False

                # An admin cannot lock themselves out.
                response = await http.patch(
                    f"/api/v1/admin/users/{admin['user_id']}",
                    headers=auth,
                    json={"role": "USER"},
                )
                assert response.status_code == 400
                response = await http.delete(f"/api/v1/admin/users/{admin['user_id']}", headers=auth)
                assert response.status_code == 400

                response = await http.get(f"/api/v1/admin/users/{created['user_id']}", headers=auth)
                assert response.json()["cards"] == []
                assert response.json()["open_session"] is None

                response = await http.delete(f"/api/v1/admin/users/{created['user_id']}", headers=auth)
                assert response.status_code == 204
                assert await users.count_documents({"user_id": created["user_id"]}) == 0
        finally:
            await clean()

    run(scenario)


def test_user_with_history_cannot_be_deleted():
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        member = await seed_user("USER")
        clean.user_ids |= {admin["user_id"], member["user_id"]}
        await transactions.insert_one(
            {
                "transaction_id": str(uuid4()),
                "user_id": member["user_id"],
                "type": "TOPUP",
                "session_id": None,
                "amount_vnd": 1000,
                "status": "COMPLETED",
                "created_at": datetime.now(UTC),
            }
        )
        try:
            async with api() as http:
                auth = await login(http, admin)
                response = await http.delete(f"/api/v1/admin/users/{member['user_id']}", headers=auth)
                assert response.status_code == 409
        finally:
            await clean()

    run(scenario)


def test_admin_links_and_unlinks_rfid_cards():
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        member = await seed_user("USER")
        clean.user_ids |= {admin["user_id"], member["user_id"]}
        uid = f"T{uuid4().hex[:7]}".upper()
        try:
            async with api() as http:
                auth = await login(http, admin)

                response = await http.post(
                    "/api/v1/admin/rfid-cards",
                    headers=auth,
                    json={"uid": uid.lower(), "label": "Thẻ test"},
                )
                assert response.status_code == 201, response.text
                card = response.json()
                clean.card_ids.add(card["card_id"])
                assert card["uid"] == uid
                assert card["status"] == "UNASSIGNED"

                response = await http.post("/api/v1/admin/rfid-cards", headers=auth, json={"uid": uid})
                assert response.status_code == 409

                # Admins do not get cards.
                response = await http.put(
                    f"/api/v1/admin/rfid-cards/{card['card_id']}/link",
                    headers=auth,
                    json={"user_id": admin["user_id"]},
                )
                assert response.status_code == 400

                response = await http.put(
                    f"/api/v1/admin/rfid-cards/{card['card_id']}/link",
                    headers=auth,
                    json={"user_id": member["user_id"]},
                )
                assert response.json()["status"] == "ACTIVE"
                assert response.json()["user_name"] == member["name"]
                _, owner = await verify_member_rfid(uid=uid)
                assert owner["user_id"] == member["user_id"]

                response = await http.get(
                    f"/api/v1/admin/rfid-cards?user_id={member['user_id']}",
                    headers=auth,
                )
                assert response.json()["total"] == 1

                response = await http.delete(
                    f"/api/v1/admin/rfid-cards/{card['card_id']}/link",
                    headers=auth,
                )
                assert response.json()["status"] == "UNASSIGNED"
                with pytest.raises(Exception, match="not linked|not found"):
                    await verify_member_rfid(uid=uid)

                # A card without UID waits for its first scan.
                response = await http.post(
                    "/api/v1/admin/rfid-cards",
                    headers=auth,
                    json={"user_id": member["user_id"]},
                )
                pending = response.json()
                clean.card_ids.add(pending["card_id"])
                assert pending["status"] == "PENDING"
        finally:
            await clean()

    run(scenario)


# UC-A03 ----------------------------------------------------------------------


def test_admin_station_crud(captured):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        clean.user_ids.add(admin["user_id"])
        station_id = f"TEST-{uuid4().hex[:6].upper()}"
        clean.station_ids.add(station_id)
        try:
            async with api() as http:
                auth = await login(http, admin)

                response = await http.post(
                    "/api/v1/admin/stations",
                    headers=auth,
                    json={
                        "station_id": station_id,
                        "name": "Trạm mới",
                        "latitude": 16.05,
                        "longitude": 108.2,
                        "is_simulated": True,
                    },
                )
                assert response.status_code == 201, response.text
                assert response.json()["availability"] == "AVAILABLE"

                response = await http.post(
                    "/api/v1/admin/stations",
                    headers=auth,
                    json={"station_id": station_id, "name": "Dup"},
                )
                assert response.status_code == 409

                response = await http.patch(
                    f"/api/v1/admin/stations/{station_id}",
                    headers=auth,
                    json={"name": "Trạm đổi tên", "address": "Đà Nẵng"},
                )
                assert response.json()["name"] == "Trạm đổi tên"

                response = await http.get(f"/api/v1/admin/stations/{station_id}", headers=auth)
                assert response.json()["current_session"] is None

                response = await http.delete(f"/api/v1/admin/stations/{station_id}", headers=auth)
                assert response.status_code == 204
                assert captured["broadcasts"][-1] == {"type": "station_deleted", "station_id": station_id}
        finally:
            await clean()

    run(scenario)


# UC-A14 ----------------------------------------------------------------------


def test_command_stop_settles_member_session(captured):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        member = await seed_user("USER", 100_000)
        station_id = await seed_station()
        clean.user_ids |= {admin["user_id"], member["user_id"]}
        clean.station_ids.add(station_id)
        uid = f"S{uuid4().hex[:7]}".upper()
        await rfid_cards.insert_one(
            {
                "card_id": f"CARD-{uid}",
                "uid": uid,
                "user_id": member["user_id"],
                "is_active": True,
                "pending": False,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )
        try:
            result = await start_member_charging(uid=uid, station_id=station_id, max_budget_vnd=None)
            await sessions.update_one(
                {"session_id": result["session"]["session_id"]},
                {"$set": {"started_at": datetime.now(UTC) - timedelta(seconds=65)}},
            )

            async with api() as http:
                auth = await login(http, admin)
                response = await http.post(
                    f"/api/v1/admin/stations/{station_id}/commands",
                    headers=auth,
                    json={"command": "STOP"},
                )
                body = response.json()
                assert response.status_code == 200, response.text
                assert body["published"] is True
                assert body["stopped_session"]["stop_reason"] == "TECHNICIAN"
                assert body["stopped_session"]["cost_vnd"] == 10
                assert body["station"]["availability"] == "AVAILABLE"
                assert captured["commands"][-1]["command"] == "STOP"
                assert captured["commands"][-1]["reason"] == "TECHNICIAN"

                user = await users.find_one({"user_id": member["user_id"]})
                assert user["balance_vnd"] == 100_000 - 10

                response = await http.post(
                    f"/api/v1/admin/stations/{station_id}/commands",
                    headers=auth,
                    json={"command": "STOP"},
                )
                assert response.status_code == 400
        finally:
            await clean()

    run(scenario)


def test_command_stop_guest_keeps_debt_then_maintenance_and_ready(captured):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        station_id = await seed_station()
        clean.user_ids.add(admin["user_id"])
        clean.station_ids.add(station_id)
        try:
            await handle_message(f"evcs/{station_id}/req", b'{"type":"GUEST_START"}')
            session = await sessions.find_one({"station_id": station_id})
            await sessions.update_one(
                {"session_id": session["session_id"]},
                {"$set": {"started_at": datetime.now(UTC) - timedelta(seconds=35)}},
            )

            async with api() as http:
                auth = await login(http, admin)

                response = await http.post(
                    f"/api/v1/admin/stations/{station_id}/commands",
                    headers=auth,
                    json={"command": "STOP"},
                )
                assert response.json()["stopped_session"]["status"] == "WAITING_PAYMENT"
                assert response.json()["station"]["availability"] == "WAITING_PAYMENT"
                assert "PAYMENT_QR" in [c["command"] for c in captured["commands"]]

                # E-Stop while waiting: error logged, station in ERROR.
                await handle_message(f"evcs/{station_id}/event", b'{"event":"ESTOP"}')
                station = await stations.find_one({"station_id": station_id})
                assert station["status"] == "ERROR"

                response = await http.get(
                    f"/api/v1/admin/errors?station_id={station_id}&resolved=false",
                    headers=auth,
                )
                errors = response.json()
                assert errors["total"] == 1
                assert errors["items"][0]["error_type"] == "E_STOP"
                assert errors["items"][0]["severity"] == "CRITICAL"

                response = await http.post(
                    f"/api/v1/admin/stations/{station_id}/commands",
                    headers=auth,
                    json={"command": "MAINTENANCE", "note": "Kiểm tra E-Stop"},
                )
                assert response.json()["station"]["status"] == "MAINTENANCE"

                response = await http.get(
                    f"/api/v1/admin/maintenance?station_id={station_id}",
                    headers=auth,
                )
                record = response.json()["items"][0]
                assert record["status"] == "IN_PROGRESS"
                assert record["technician_id"] == admin["user_id"]
                assert record["error_id"] == errors["items"][0]["error_id"]

                # The guest still owes money, so the station cannot be released yet.
                response = await http.post(
                    f"/api/v1/admin/stations/{station_id}/commands",
                    headers=auth,
                    json={"command": "READY"},
                )
                assert response.status_code == 409

                payment = await payment_intents.find_one({"session_id": session["session_id"]})
                await http.post(f"/api/v1/guest/payments/{payment['payment_id']}/pay")

                response = await http.post(
                    f"/api/v1/admin/stations/{station_id}/commands",
                    headers=auth,
                    json={"command": "READY"},
                )
                assert response.status_code == 200, response.text
                assert response.json()["station"]["availability"] == "AVAILABLE"

                record = await maintenance_records.find_one({"station_id": station_id})
                assert record["status"] == "COMPLETED"
                assert await error_logs.count_documents(
                    {"station_id": station_id, "resolved_at": None}
                ) == 0

                response = await http.post(
                    f"/api/v1/admin/stations/{station_id}/commands",
                    headers=auth,
                    json={"command": "LOCK"},
                )
                assert captured["commands"][-1]["command"] == "LOCK"

            commands = [
                a["metadata"]["command"]
                async for a in audit_logs.find(
                    {"actor_user_id": admin["user_id"], "action": "STATION_COMMAND"}
                ).sort("created_at", 1)
            ]
            assert commands == ["STOP", "MAINTENANCE", "READY", "LOCK"]
        finally:
            await clean()

    run(scenario)


# UC-A04 ----------------------------------------------------------------------


def test_price_change_applies_to_new_sessions_only(captured):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        running_station = await seed_station()
        new_station = await seed_station()
        clean.user_ids.add(admin["user_id"])
        clean.station_ids |= {running_station, new_station}
        try:
            async with api() as http:
                auth = await login(http, admin)
                original = (await http.get("/api/v1/admin/prices/current", headers=auth)).json()
                assert original["tick_cost_vnd"] == 3

                await handle_message(f"evcs/{running_station}/req", b'{"type":"GUEST_START"}')

                response = await http.post(
                    "/api/v1/admin/prices",
                    headers=auth,
                    json={"price_per_kwh_vnd": 8000, "note": "test"},
                )
                assert response.status_code == 201, response.text
                try:
                    current = (await http.get("/api/v1/admin/prices/current", headers=auth)).json()
                    assert current["price_per_kwh_vnd"] == 8000
                    assert current["tick_cost_vnd"] == 7

                    response = await http.post(
                        "/api/v1/admin/prices",
                        headers=auth,
                        json={"price_per_kwh_vnd": 8000},
                    )
                    assert response.status_code == 400
                    response = await http.post(
                        "/api/v1/admin/prices",
                        headers=auth,
                        json={"price_per_kwh_vnd": 100},
                    )
                    assert response.status_code == 400

                    await handle_message(f"evcs/{new_station}/req", b'{"type":"GUEST_START"}')
                    running = await sessions.find_one({"station_id": running_station})
                    new = await sessions.find_one({"station_id": new_station})
                    assert running["price_per_kwh_vnd"] == original["price_per_kwh_vnd"]
                    assert new["price_per_kwh_vnd"] == 8000

                    history = (await http.get("/api/v1/admin/prices?limit=2", headers=auth)).json()
                    assert history["items"][0]["price_per_kwh_vnd"] == 8000
                    assert history["items"][0]["effective_to"] is None
                    assert history["items"][1]["effective_to"] is not None
                    assert await price_history.count_documents({"effective_to": None}) == 1
                finally:
                    await http.post(
                        "/api/v1/admin/prices",
                        headers=auth,
                        json={"price_per_kwh_vnd": original["price_per_kwh_vnd"]},
                    )
        finally:
            await clean()

    run(scenario)


# UC-A05..A13 -----------------------------------------------------------------


def test_admin_lists_live_views_and_dashboard(captured):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        member = await seed_user("USER", 50_000)
        station_id = await seed_station()
        clean.user_ids |= {admin["user_id"], member["user_id"]}
        clean.station_ids.add(station_id)
        now = datetime.now(UTC)
        session_id = str(uuid4())
        await sessions.insert_one(
            {
                "session_id": session_id,
                "station_id": station_id,
                "type": "MEMBER",
                "user_id": member["user_id"],
                "status": "ACTIVE",
                "budget_mode": "BALANCE",
                "max_budget_vnd": 50_000,
                "energy_kwh": Decimal128("0"),
                "power_kw": Decimal128("0.15"),
                "price_per_kwh_vnd": 4000,
                "cost_vnd": 0,
                "stop_reason": None,
                "started_at": now - timedelta(seconds=95),
                "ended_at": None,
                "connector_locked": False,
                "created_at": now,
                "updated_at": now,
            }
        )
        await stations.update_one(
            {"station_id": station_id},
            {"$set": {"status": "CHARGING", "current_session_id": session_id}},
        )
        try:
            async with api() as http:
                auth = await login(http, admin)

                # A09: live sessions include usage up to now.
                live = (await http.get("/api/v1/admin/monitor/sessions", headers=auth)).json()
                mine = next(s for s in live if s["session_id"] == session_id)
                assert mine["cost_vnd"] == 15
                assert mine["user_name"] == member["name"]
                assert mine["station_name"] == "Admin Test Station"

                # A08: live stations carry their current session.
                board = (await http.get("/api/v1/admin/monitor/stations", headers=auth)).json()
                tile = next(s for s in board if s["station_id"] == station_id)
                assert tile["availability"] == "CHARGING"
                assert tile["current_session"]["session_id"] == session_id

                # A05
                response = await http.get(
                    f"/api/v1/admin/sessions?station_id={station_id}&status=ACTIVE",
                    headers=auth,
                )
                assert response.json()["total"] == 1
                detail = (await http.get(f"/api/v1/admin/sessions/{session_id}", headers=auth)).json()
                assert detail["transactions"] == []

                # A06 / A07
                response = await http.get(
                    f"/api/v1/admin/transactions?user_id={member['user_id']}",
                    headers=auth,
                )
                assert response.json()["total"] == 0
                assert (await http.get("/api/v1/admin/payments", headers=auth)).status_code == 200
                assert (await http.get("/api/v1/admin/wallet-holds", headers=auth)).status_code == 200

                # A10
                board = (await http.get("/api/v1/admin/dashboard", headers=auth)).json()
                assert board["sessions_active"] >= 1
                assert len(board["last_7_days"]) == 7
                assert sum(board["stations_by_availability"].values()) == board["stations_total"]
                assert any(s["session_id"] == session_id for s in board["live_sessions"])

                # A13: every action above that changed data is audited.
                response = await http.get(
                    f"/api/v1/admin/audit-logs?actor_user_id={admin['user_id']}",
                    headers=auth,
                )
                assert [a["action"] for a in response.json()["items"]] == ["ADMIN_LOGIN"]
        finally:
            await clean()

    run(scenario)


def test_resolve_error_log(captured):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        station_id = await seed_station()
        clean.user_ids.add(admin["user_id"])
        clean.station_ids.add(station_id)
        try:
            await handle_message(f"evcs/{station_id}/event", b'{"event":"OVER_TEMPERATURE"}')
            error = await error_logs.find_one({"station_id": station_id})
            assert error["error_type"] == "OVER_TEMPERATURE"
            assert (await stations.find_one({"station_id": station_id}))["status"] == "ERROR"

            async with api() as http:
                auth = await login(http, admin)
                response = await http.post(
                    f"/api/v1/admin/errors/{error['error_id']}/resolve",
                    headers=auth,
                )
                assert response.json()["resolved_by_name"] == admin["name"]
                response = await http.post(
                    f"/api/v1/admin/errors/{error['error_id']}/resolve",
                    headers=auth,
                )
                assert response.status_code == 400
        finally:
            await clean()

    run(scenario)
