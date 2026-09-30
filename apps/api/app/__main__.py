"""Run the API: `python -m app` from apps/api.

The default asyncio loop is forced because uvloop intermittently fails to register
the aiomqtt socket ("File descriptor ... is used by transport"), which silently
leaves the backend without MQTT.
"""
import uvicorn

from app.core.config import get_settings

settings = get_settings()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        loop="asyncio",
        reload=settings.app_env == "development",
    )
