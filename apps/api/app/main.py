import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.dependencies import user_from_token
from app.db.migrations import apply_migrations
from app.db.mongodb import check_database, close_database
from app.modules.admin.router import router as admin_router
from app.modules.auth.router import router as auth_router
from app.modules.charging.ticker import run_session_ticker
from app.modules.guest.router import router as guest_router
from app.modules.member.router import router as member_router
from app.modules.station.router import router as station_router
from app.mqtt.client import mqtt_service
from app.mqtt.watchdog import run_station_watchdog
from app.websocket.manager import manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_database()
    await apply_migrations()

    # Handlers push their own filtered WebSocket events; raw MQTT results are not
    # broadcast because they can contain member data (RFID owner, balance).
    mqtt_task = asyncio.create_task(mqtt_service.run())
    watchdog_task = asyncio.create_task(run_station_watchdog())
    ticker_task = asyncio.create_task(run_session_ticker())

    yield

    for task in (mqtt_task, watchdog_task, ticker_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await close_database()


app = FastAPI(
    title="SECO API",
    version="0.1.0",
    description="SECO Smart EV Charging - shared backend core",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(station_router)
app.include_router(member_router)
app.include_router(guest_router)
app.include_router(admin_router)

@app.get("/health", tags=["Health"])
async def health():
    await check_database()
    return {
        "status": "ok",
        "service": "seco-api",
        "database": "ok",
        "mqtt": "starting",
    }


async def _websocket_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        user = await user_from_token(token)
    except HTTPException:
        return None
    return user if user.get("is_active") else None


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = None):
    """Realtime updates. Pass ?token=<JWT> to also receive your own session events."""
    user = await _websocket_user(token)
    if token and user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, user)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except RuntimeError:
        manager.disconnect(websocket)
