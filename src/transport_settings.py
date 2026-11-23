from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from PySide6.QtCore import QSettings  # type: ignore

from src.transport.gorizontvsprotocol import (
    FIELD_NAMES as GORIZONTVS_FIELDS,
    GorizontVSSettings,
    normalize_gorizontvs_settings,
)

__all__ = ["SpiceSettings", "GorizontVSSettings", "TransportSettingsStore"]


@dataclass(frozen=True)
class SpiceSettings:
    multi_monitor_enabled: bool = False
    monitor_count: int = 1

    webdav_enabled: bool = False
    webdav_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
 


def _monitor_count(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 1
    return max(1, min(4, count))


class TransportSettingsStore:
    ROOT = "profile_transport_settings"

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings

    def _prefix(self, profile_id: str, transport: str) -> str:
        return f"{self.ROOT}/{profile_id}/{transport}"
 
    def load_spice(self, profile_id: str) -> SpiceSettings:
        prefix = self._prefix(profile_id, "spice")
 
        return SpiceSettings(
            multi_monitor_enabled=_to_bool(
                self._settings.value(f"{prefix}/multi_monitor_enabled", False)
            ),
            monitor_count=_monitor_count(
                self._settings.value(f"{prefix}/monitor_count", 1)
            ),
            webdav_enabled=_to_bool(
                self._settings.value(f"{prefix}/webdav_enabled", False)
            ),
            webdav_path=str(
                self._settings.value(f"{prefix}/webdav_path", "") or ""
            ),
        )
 
    def save_spice(self, profile_id: str, value: SpiceSettings) -> None:
        prefix = self._prefix(profile_id, "spice")
        self._settings.setValue(
            f"{prefix}/multi_monitor_enabled", value.multi_monitor_enabled
        )
        self._settings.setValue(
            f"{prefix}/monitor_count", _monitor_count(value.monitor_count)
        )
        self._settings.setValue(f"{prefix}/webdav_enabled", value.webdav_enabled)
        self._settings.setValue(f"{prefix}/webdav_path", value.webdav_path)
        self._settings.remove(f"{prefix}/webdav_path_type")

        self._settings.sync()

    def load_gorizontvs(self, profile_id: str) -> GorizontVSSettings:
        prefix = self._prefix(profile_id, "gorizontvs")
        raw = {name: self._settings.value(f"{prefix}/{name}") for name in GORIZONTVS_FIELDS}
        return normalize_gorizontvs_settings({k: v for k, v in raw.items() if v is not None})

    def save_gorizontvs(self, profile_id: str, value: GorizontVSSettings) -> None:
        prefix = self._prefix(profile_id, "gorizontvs")
        for name, item in normalize_gorizontvs_settings(value.to_dict()).to_dict().items():
            self._settings.setValue(f"{prefix}/{name}", item)
        self._settings.sync()

    def remove_profile(self, profile_id: str) -> None:
        self._settings.remove(f"{self.ROOT}/{profile_id}")
        self._settings.sync()