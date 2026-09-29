def command(station_id: str) -> str:
    return f"evcs/{station_id}/cmd"


def request(station_id: str) -> str:
    return f"evcs/{station_id}/req"


def response(station_id: str) -> str:
    return f"evcs/{station_id}/res"


def telemetry(station_id: str) -> str:
    return f"evcs/{station_id}/telemetry"


def event(station_id: str) -> str:
    return f"evcs/{station_id}/event"


def cmd_ack(station_id: str) -> str:
    return f"evcs/{station_id}/cmd_ack"


def status(station_id: str) -> str:
    return f"evcs/{station_id}/status"
