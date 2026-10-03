"""Catalogue of in-app notifications: who gets what, and the text they see.

Member (USER, mobile app)
    CHARGING_COMPLETED    a session ended: energy, cost, why it stopped
    LOW_BALANCE           balance left after a session is below the threshold
    TOPUP_SUCCEEDED       wallet top-up credited
    CARD_ASSIGNED         the desk gave the member a (new) RFID card
    CARD_BLOCKED          a card was locked or reported lost (by the owner or an admin)
    CARD_UNLOCKED         a locked card works again
    SUPPORT_TICKET_ACCEPTED / SUPPORT_TICKET_RESOLVED   progress of a help request
Technician (mobile app)
    SUPPORT_TICKET_CREATED   a member needs help at a station
    STATION_ERROR            a station logged an error (E-Stop, overheat, lost link...)
Every role
    PASSWORD_CHANGED      security notice after a password change or reset
    TEMP_PASSWORD_ISSUED  an admin issued a temporary password for the account
"""

import functools
import logging

from app.core.config import get_settings
from app.core.mongo_types import from_decimal128
from app.modules.notifications.service import notify, notify_role, notify_user

logger = logging.getLogger(__name__)
settings = get_settings()


def best_effort(func):
    """A notification must never break the action that triggered it."""

    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            await func(*args, **kwargs)
        except Exception:
            logger.exception("Notification %s failed", func.__name__)

    return wrapper

_STOP_REASONS = {
    "UNPLUG": "bạn đã dừng sạc",
    "MAX_BUDGET": "đã đạt mức ngân sách bạn đặt",
    "INSUFFICIENT_BALANCE": "số dư ví không đủ để sạc tiếp",
    "TECHNICIAN": "kỹ thuật viên đã dừng phiên",
    "OVER_TEMPERATURE": "trạm quá nhiệt",
    "E_STOP": "nút dừng khẩn cấp được nhấn",
    "SYSTEM_ERROR": "trạm gặp lỗi",
}

_SEVERITY = {"WARNING": "Cảnh báo", "ERROR": "Lỗi", "CRITICAL": "Lỗi nghiêm trọng"}

_CATEGORIES = {
    "CANNOT_START": "Không bắt đầu sạc được",
    "CHARGING_INTERRUPTED": "Phiên sạc bị gián đoạn",
    "CONNECTOR_STUCK": "Không rút được đầu sạc",
    "PAYMENT": "Thanh toán",
    "CARD": "Thẻ RFID",
    "STATION_DAMAGED": "Trạm bị hư hỏng",
    "OTHER": "Khác",
}


def _vnd(amount: int | None) -> str:
    return f"{int(amount or 0):,}".replace(",", ".") + " đ"


def category_label(category: str) -> str:
    return _CATEGORIES.get(category, category)


@best_effort
async def charging_completed(session: dict, transaction: dict | None) -> None:
    user_id = session.get("user_id")
    if not user_id:
        return  # guest session: no account to notify

    energy_kwh = session.get("energy_kwh")
    energy = float(from_decimal128(energy_kwh)) if energy_kwh is not None else 0.0
    amount = (transaction or {}).get("amount_vnd", session.get("cost_vnd"))
    reason = _STOP_REASONS.get(session.get("stop_reason") or "", "phiên đã kết thúc")
    await notify(
        user_id=user_id,
        role="USER",
        type="CHARGING_COMPLETED",
        title="Phiên sạc đã kết thúc",
        body=f"Trạm {session['station_id']}: {energy:.2f} kWh, {_vnd(amount)}. Lý do: {reason}.",
        data={
            "session_id": session["session_id"],
            "station_id": session["station_id"],
            "stop_reason": session.get("stop_reason"),
            "transaction_id": (transaction or {}).get("transaction_id"),
        },
    )

    balance = (transaction or {}).get("balance_after_vnd")
    if balance is not None and balance < settings.low_balance_threshold_vnd:
        await notify(
            user_id=user_id,
            role="USER",
            type="LOW_BALANCE",
            title="Số dư ví thấp",
            body=f"Số dư còn {_vnd(balance)}. Nạp thêm để không bị gián đoạn lần sạc sau.",
            data={"balance_vnd": balance},
        )


@best_effort
async def topup_succeeded(user_id: str, transaction: dict) -> None:
    await notify(
        user_id=user_id,
        role="USER",
        type="TOPUP_SUCCEEDED",
        title="Nạp tiền thành công",
        body=(
            f"Đã cộng {_vnd(transaction['amount_vnd'])} vào ví. "
            f"Số dư: {_vnd(transaction.get('balance_after_vnd'))}."
        ),
        data={"transaction_id": transaction["transaction_id"]},
    )

    # Lower-case ('thẻ "Xe nhà"', 'thẻ ...1F7C') so it fits inside a sentence.
def _card_name(card: dict) -> str:
    # Lower-case ("thẻ \"Xe nhà\"", "thẻ ...1F7C") so it fits inside a sentence.
    if card.get("label"):
        return f"thẻ \"{card['label']}\""
    uid = card.get("uid") or ""
    return f"thẻ ...{uid[-4:]}" if uid else "thẻ RFID"


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


@best_effort
async def card_assigned(owner: dict, card: dict) -> None:
    await notify_user(
        owner,
        type="CARD_ASSIGNED",
        title="Thẻ RFID mới",
        body=f"{_capitalized(_card_name(card))} đã được gán cho tài khoản của bạn.",
        data={"card_id": card["card_id"]},
    )


@best_effort
async def card_blocked(owner: dict, card: dict, *, reason: str, by_admin: bool) -> None:
    who = "Quản trị viên đã khóa" if by_admin else "Bạn đã khóa"
    if reason == "LOST":
        body = f"{_capitalized(_card_name(card))} đã được báo mất và bị vô hiệu hóa. Liên hệ quầy để cấp thẻ mới."
    else:
        body = f"{who} {_card_name(card)}. Thẻ không dùng được để sạc cho đến khi mở khóa."
    await notify_user(
        owner,
        type="CARD_BLOCKED",
        title="Thẻ RFID bị khóa",
        body=body,
        data={"card_id": card["card_id"], "reason": reason},
    )


@best_effort
async def card_unlocked(owner: dict, card: dict) -> None:
    await notify_user(
        owner,
        type="CARD_UNLOCKED",
        title="Thẻ RFID đã mở khóa",
        body=f"{_capitalized(_card_name(card))} đã dùng lại được để sạc.",
        data={"card_id": card["card_id"]},
    )


@best_effort
async def support_ticket_created(ticket: dict, member: dict, station_name: str | None) -> None:
    station = station_name or ticket["station_id"]
    await notify_role(
        "TECHNICIAN",
        type="SUPPORT_TICKET_CREATED",
        title=f"Yêu cầu hỗ trợ tại {station}",
        body=f"{member['name']}: {category_label(ticket['category'])}. {ticket['description']}".strip(),
        data={"ticket_id": ticket["ticket_id"], "station_id": ticket["station_id"]},
    )


@best_effort
async def support_ticket_accepted(ticket: dict, technician: dict) -> None:
    await notify(
        user_id=ticket["user_id"],
        role="USER",
        type="SUPPORT_TICKET_ACCEPTED",
        title="Kỹ thuật viên đã nhận yêu cầu",
        body=f"{technician['name']} đang xử lý yêu cầu hỗ trợ tại trạm {ticket['station_id']}.",
        data={"ticket_id": ticket["ticket_id"], "station_id": ticket["station_id"]},
    )


@best_effort
async def support_ticket_resolved(ticket: dict, technician: dict) -> None:
    note = ticket.get("resolution_note")
    await notify(
        user_id=ticket["user_id"],
        role="USER",
        type="SUPPORT_TICKET_RESOLVED",
        title="Yêu cầu hỗ trợ đã được xử lý",
        body=f"{technician['name']}: {note}" if note else "Yêu cầu hỗ trợ của bạn đã được xử lý.",
        data={"ticket_id": ticket["ticket_id"], "station_id": ticket["station_id"]},
    )


@best_effort
async def station_error(error: dict) -> None:
    severity = _SEVERITY.get(error.get("severity", ""), "Lỗi")
    await notify_role(
        "TECHNICIAN",
        type="STATION_ERROR",
        title=f"{severity} tại trạm {error['station_id']}",
        body=error.get("message") or error.get("error_type", ""),
        data={
            "error_id": error["error_id"],
            "station_id": error["station_id"],
            "error_type": error.get("error_type"),
            "severity": error.get("severity"),
            "session_id": error.get("session_id"),
        },
    )


@best_effort
async def password_changed(user: dict, *, via_reset: bool) -> None:
    how = "được đặt lại bằng mật khẩu tạm" if via_reset else "vừa được thay đổi"
    await notify_user(
        user,
        type="PASSWORD_CHANGED",
        title="Mật khẩu đã thay đổi",
        body=(
            f"Mật khẩu tài khoản của bạn {how}. Các thiết bị khác đã được đăng xuất. "
            "Nếu không phải bạn, hãy liên hệ quản trị viên ngay."
        ),
    )


@best_effort
async def temporary_password_issued(user: dict) -> None:
    await notify_user(
        user,
        type="TEMP_PASSWORD_ISSUED",
        title="Mật khẩu tạm đã được cấp",
        body=(
            "Quản trị viên vừa cấp mật khẩu tạm cho tài khoản của bạn. Mật khẩu hiện tại vẫn dùng được "
            "cho tới khi bạn đặt mật khẩu mới. Nếu bạn không yêu cầu, hãy liên hệ quản trị viên."
        ),
    )
