from __future__ import annotations

import json
import os
from typing import Any


DEFAULT_MAP_CENTER = [38.459517, -8.868642]
DEFAULT_ZOOM = 11
DEFAULT_PORT_LABEL = "Porto de Setubal"
DEFAULT_DASHBOARD_URL = "https://www.vesselfinder.com/embed"
DEFAULT_SCRIPT_URL = "https://www.vesselfinder.com/aismap.js"


def _parse_json_env(name: str, fallback: Any) -> Any:
    raw = os.getenv(name, "").strip()
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return fallback


class AISMapService:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.provider_name = "VesselFinder"
        self.provider_mode = "embed"
        self.map_center = _parse_json_env("AIS_MAP_CENTER", DEFAULT_MAP_CENTER)
        self.zoom_start = int(os.getenv("AIS_MAP_ZOOM", str(DEFAULT_ZOOM)))
        self.port_label = os.getenv("AIS_PORT_LABEL", DEFAULT_PORT_LABEL).strip() or DEFAULT_PORT_LABEL
        self.names = os.getenv("AIS_VESSELFINDER_NAMES", "1").strip() == "1"
        self.dashboard_url = DEFAULT_DASHBOARD_URL
        self.script_url = DEFAULT_SCRIPT_URL

    def dashboard_context(self) -> dict[str, Any]:
        embed = self.embed_context()
        return {
            "provider_name": self.provider_name,
            "provider_mode": self.provider_mode,
            "enabled": True,
            "configured": True,
            "running": False,
            "ship_count": None,
            "last_save_label": "n/d",
            "last_message_label": "n/d",
            "last_error": "",
            "message_count": 0,
            "bounding_box": None,
            "filters_ship_mmsi": [],
            "ships_preview": [],
            "map_available": True,
            "map_url": "/embed/vesselfinder/setubal",
            "dashboard_url": self.dashboard_url,
            "poll_interval_seconds": 0,
            "area_endpoint": self.script_url,
            "rate_limit": {},
            "status_label": "Embed publico",
            "source_label": "sem API",
            "embed": embed,
        }

    def embed_context(self) -> dict[str, Any]:
        latitude = float(self.map_center[0]) if len(self.map_center) > 0 else float(DEFAULT_MAP_CENTER[0])
        longitude = float(self.map_center[1]) if len(self.map_center) > 1 else float(DEFAULT_MAP_CENTER[1])
        return {
            "width": "100%",
            "height": "100%",
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "zoom": str(self.zoom_start),
            "names": self.names,
            "port_label": self.port_label,
            "dashboard_url": self.dashboard_url,
            "script_url": self.script_url,
        }

    def ensure_map_file(self) -> None:
        return

    def start_background(self) -> None:
        return


def create_ais_service(base_dir: str) -> AISMapService:
    return AISMapService(base_dir=base_dir)
