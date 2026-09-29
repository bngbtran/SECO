import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.db.indexes import create_indexes
from app.db.mongodb import check_database, close_database
from app.mqtt.client import mqtt_service
from app.mqtt.watchdog import run_station_watchdog
from app.websocket.manager import manager
from app.modules.auth.router import router as auth_router
from app.modules.station.router import router as station_router


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await check_database()
    await create_indexes()

    mqtt_service.add_broadcast_handler(manager.broadcast)
    mqtt_task = asyncio.create_task(mqtt_service.run())
    watchdog_task = asyncio.create_task(run_station_watchdog())

    yield

    mqtt_task.cancel()
    watchdog_task.cancel()
    try:
        await mqtt_task
    except asyncio.CancelledError:
        pass

    try:
        await watchdog_task
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


@app.get("/health", tags=["Health"])
async def health():
    await check_database()
    return {
        "status": "ok",
        "service": "seco-api",
        "database": "ok",
        "mqtt": "starting",
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
