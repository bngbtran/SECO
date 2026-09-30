import logging
from typing import Any

from bson.decimal128 import Decimal128
from fastapi.encoders import jsonable_encoder

from app.mqtt import topics

logger = logging.getLogger(__name__)


def to_jsonable(value: Any) -> Any:
    return jsonable_encoder(
        value,
        custom_encoder={Decimal128: lambda v: float(v.to_decimal())},
    )


async def publish_station_command(station_id: str, payload: dict[str, Any]) -> bool:
    """Publish to evcs/{station_id}/cmd; False when the broker is not connected."""
    # Imported lazily: app.mqtt.client imports the MQTT handlers, which import this module.
    from app.mqtt.client import mqtt_service

    try:
        await mqtt_service.publish(topics.command(station_id), to_jsonable(payload))
    except RuntimeError:
        logger.warning("MQTT not connected; command not sent station=%s", station_id)
        return False
    return True
