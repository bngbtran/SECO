from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.core.exceptions import ResourceNotFoundError
from app.db.mongodb import get_collection
from app.modules.admin import card_registration
from app.modules.member.service import verify_member_rfid
from app.mqtt.handlers import handle_message
from app.websocket.manager import manager
from tests.shared_loop import TEST_LOOP
from tests.test_admin import Cleanup, api, login, seed_user

registrations = get_collection("card_registrations")
readers = get_collection("rfid_readers")
rfid_cards = get_collection("rfid_cards")

READER = get_settings().admin_reader_id


def run(scenario):
    TEST_LOOP.run_until_complete(scenario())


@pytest.fixture
def reader(monkeypatch):
    """Capture what the API sends to the desk reader and to WebSocket clients."""
    data = {"cmd": [], "res": [], "broadcasts": []}

    async def fake_publish(reader_id, kind, payload):
        data[kind].append(payload)
        return True

    async def fake_broadcast(payload):
        data["broadcasts"].append(payload)

    monkeypatch.setattr(card_registration, "publish_reader", fake_publish)
    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    yield data
    TEST_LOOP.run_until_complete(registrations.delete_many({"reader_id": READER}))


async def tap(uid: str) -> None:
    await handle_message(f"seco/reader/{READER}/scan", f'{{"uid":"{uid}"}}'.encode())


async def add_card(uid: str, user_id: str | None, card_id: str | None = None) -> str:
    card_id = card_id or f"CARD-T{uuid4().hex[:6].upper()}"
    await rfid_cards.insert_one(
        {
            "card_id": card_id,
            "uid": uid,
            "user_id": user_id,
            "is_active": user_id is not None,
            "pending": False,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    return card_id


def new_uid(prefix: str = "T") -> str:
    return f"{prefix}{uuid4().hex[:7]}".upper()


def test_new_member_gets_a_card_scanned_on_the_desk_reader(reader):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        member = await seed_user("USER")
        other = await seed_user("USER")
        clean.user_ids |= {admin["user_id"], member["user_id"], other["user_id"]}
        taken = new_uid()
        clean.card_ids.add(await add_card(taken, other["user_id"]))
        uid = new_uid("N")
        try:
            # The reader announces itself.
            await handle_message(f"seco/reader/{READER}/hb", b'{"fw":"test"}')

            async with api() as http:
                auth = await login(http, admin)
                status = (await http.get("/api/v1/admin/card-registrations/reader", headers=auth)).json()
                assert status["reader_id"] == READER
                assert status["is_online"] is True

                response = await http.post(
                    "/api/v1/admin/card-registrations",
                    headers=auth,
                    json={"user_id": member["user_id"]},
                )
                assert response.status_code == 201, response.text
                reg = response.json()
                assert reg["status"] == "WAITING"
                assert reg["mode"] == "ASSIGN"
                assert reader["cmd"][-1]["command"] == "SCAN"
                assert reader["cmd"][-1]["user_name"] == member["name"]

                # Someone else's card is refused; the admin taps another one.
                await tap(taken)
                assert reader["res"][-1]["ok"] is False
                assert reader["res"][-1]["error"] == "UID_TAKEN"
                current = (await http.get(f"/api/v1/admin/card-registrations/{reg['registration_id']}", headers=auth)).json()
                assert current["status"] == "WAITING"
                assert other["name"] in current["last_error"]

                await tap(uid.lower())
                assert reader["res"][-1]["ok"] is True
                assert reader["res"][-1]["uid"] == uid
                event = reader["broadcasts"][-1]
                assert event["type"] == "card_registration_updated"
                assert event["status"] == "SCANNED"
                assert event["uid"] == uid

                response = await http.post(
                    f"/api/v1/admin/card-registrations/{reg['registration_id']}/complete",
                    headers=auth,
                    json={"label": "Thẻ chính"},
                )
                assert response.status_code == 200, response.text
                done = response.json()
                assert done["status"] == "COMPLETED"
                assert reader["cmd"][-1]["command"] == "DONE"
                assert reader["cmd"][-1]["uid"] == uid

            # The member can now start charging with this card.
            card, owner = await verify_member_rfid(uid=uid)
            assert owner["user_id"] == member["user_id"]
            assert card["label"] == "Thẻ chính"
        finally:
            await clean()

    run(scenario)


def test_replace_card_disables_the_old_one(reader):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        member = await seed_user("USER")
        clean.user_ids |= {admin["user_id"], member["user_id"]}
        old_uid = new_uid("O")
        old_card = await add_card(old_uid, member["user_id"])
        clean.card_ids.add(old_card)
        new = new_uid("R")
        try:
            async with api() as http:
                auth = await login(http, admin)
                reg = (
                    await http.post(
                        "/api/v1/admin/card-registrations",
                        headers=auth,
                        json={"user_id": member["user_id"], "mode": "REPLACE", "replace_card_id": old_card},
                    )
                ).json()
                assert reg["replace_uid"] == old_uid

                # Tapping the card being replaced is refused.
                await tap(old_uid)
                assert reader["res"][-1]["ok"] is False

                await tap(new)
                response = await http.post(
                    f"/api/v1/admin/card-registrations/{reg['registration_id']}/complete",
                    headers=auth,
                    json={},
                )
                assert response.status_code == 200, response.text

            _, owner = await verify_member_rfid(uid=new)
            assert owner["user_id"] == member["user_id"]
            with pytest.raises(ResourceNotFoundError):
                await verify_member_rfid(uid=old_uid)
            old = await rfid_cards.find_one({"card_id": old_card})
            assert old["user_id"] is None
            assert old["is_active"] is False
        finally:
            await clean()

    run(scenario)


def test_unlinked_card_can_be_reused(reader):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        member = await seed_user("USER")
        clean.user_ids |= {admin["user_id"], member["user_id"]}
        uid = new_uid("U")
        card_id = await add_card(uid, None)
        clean.card_ids.add(card_id)
        try:
            async with api() as http:
                auth = await login(http, admin)
                reg = (
                    await http.post("/api/v1/admin/card-registrations", headers=auth, json={"user_id": member["user_id"]})
                ).json()
                await tap(uid)
                assert reader["res"][-1]["ok"] is True
                done = (
                    await http.post(
                        f"/api/v1/admin/card-registrations/{reg['registration_id']}/complete",
                        headers=auth,
                        json={},
                    )
                ).json()
                # The existing card document is reused rather than duplicated.
                assert done["card_id"] == card_id
            _, owner = await verify_member_rfid(uid=uid)
            assert owner["user_id"] == member["user_id"]
        finally:
            await clean()

    run(scenario)


def test_scan_without_request_newest_request_wins_and_expiry(reader):
    async def scenario():
        clean = Cleanup()
        admin = await seed_user("ADMIN", 0)
        first = await seed_user("USER")
        second = await seed_user("USER")
        clean.user_ids |= {admin["user_id"], first["user_id"], second["user_id"]}
        try:
            await tap(new_uid())
            assert reader["res"][-1]["error"] == "NO_REQUEST"

            async with api() as http:
                auth = await login(http, admin)
                a = (await http.post("/api/v1/admin/card-registrations", headers=auth, json={"user_id": first["user_id"]})).json()
                b = (await http.post("/api/v1/admin/card-registrations", headers=auth, json={"user_id": second["user_id"]})).json()

                older = (await http.get(f"/api/v1/admin/card-registrations/{a['registration_id']}", headers=auth)).json()
                assert older["status"] == "CANCELLED"
                assert reader["cmd"][-1]["command"] == "SCAN"
                assert reader["cmd"][-1]["user_name"] == second["name"]

                await registrations.update_one(
                    {"registration_id": b["registration_id"]},
                    {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
                )
                expired = (await http.get(f"/api/v1/admin/card-registrations/{b['registration_id']}", headers=auth)).json()
                assert expired["status"] == "EXPIRED"
                assert reader["cmd"][-1]["command"] == "CANCEL"

                response = await http.post(
                    f"/api/v1/admin/card-registrations/{b['registration_id']}/complete",
                    headers=auth,
                    json={},
                )
                assert response.status_code == 409

            # Last will from the broker marks the reader offline.
            await handle_message(f"seco/reader/{READER}/status", b"OFFLINE")
            status = await card_registration.get_reader(READER)
            assert status["is_online"] is False
        finally:
            await clean()

    run(scenario)
