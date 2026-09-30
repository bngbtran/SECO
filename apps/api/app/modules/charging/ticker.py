import asyncio
import logging

from app.core.config import get_settings
from app.modules.guest.realtime import tick_guest_sessions, tick_payment_expiry
from app.modules.member.realtime import tick_active_sessions

logger = logging.getLogger(__name__)
settings = get_settings()


async def run_session_ticker() -> None:
    """Bill running sessions and expire guest QRs, independent of station telemetry."""
    while True:
        for step in (tick_active_sessions, tick_guest_sessions, tick_payment_expiry):
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Session ticker step %s failed", step.__name__)

        await asyncio.sleep(settings.session_ticker_interval_seconds)
