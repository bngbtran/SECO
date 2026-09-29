import asyncio
import json
import logging
import ssl
from collections.abc import Awaitable, Callable

import aiomqtt

from app.core.config import get_settings
from app.mqtt.handlers import handle_message

logger = logging.getLogger(__name__)
settings = get_settings()


BroadcastHandler = Callable[[dict], Awaitable[None]]


class MQTTService:
    def __init__(self) -> None:
        self._client: aiomqtt.Client | None = None
        self._broadcast_handlers: list[BroadcastHandler] = []

    def add_broadcast_handler(self, handler: BroadcastHandler) -> None:
        self._broadcast_handlers.append(handler)

    def _tls_context(self):
        if not settings.mqtt_tls:
            return None
        context = ssl.create_default_context()
        return context

    async def publish(self, topic: str, payload: dict) -> None:
        if self._client is None:
            raise RuntimeError("MQTT is not connected")
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await self._client.publish(topic, body)

    async def run(self) -> None:
        while True:
            try:
                client_kwargs = {
                    "hostname": settings.mqtt_host,
                    "port": settings.mqtt_port,
                    "identifier": settings.mqtt_client_id,
                    "username": settings.mqtt_username,
                    "password": settings.mqtt_password,
                    "tls_context": self._tls_context(),
                }
                # aiomqtt v2 accepts these keyword arguments and is pinned <3 in pyproject.
                async with aiomqtt.Client(**client_kwargs) as client:
                    self._client = client
                    subscriptions = (
                        "evcs/+/hb",
                        "evcs/+/telemetry",
                        "evcs/+/event",
                        "evcs/+/req",
                        "evcs/+/cmd_ack",
                        "evcs/+/status",
                        "evcs/+/res",
                    )
                    for topic in subscriptions:
                        await client.subscribe(topic)

                    logger.info(
                        "MQTT connected to %s:%s",
                        settings.mqtt_host,
                        settings.mqtt_port,
                    )

                    async for message in client.messages:
                        try:
                            result = await handle_message(
                                str(message.topic),
                                bytes(message.payload),
                            )
                            if result is not None:
                                for handler in self._broadcast_handlers:
                                    await handler(result)
                        except Exception:
                            logger.exception("MQTT message handling failed: %s", message.topic)

            except asyncio.CancelledError:
                self._client = None
                raise
            except Exception:
                logger.exception("MQTT connection error")
                self._client = None
                await asyncio.sleep(3)


mqtt_service = MQTTService()
