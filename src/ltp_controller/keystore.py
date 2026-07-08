"""Per-device pre-shared key store for Layer 2 device auth.

Keys live in ~/.config/ltp/keys.yaml (0600), protected by Layer 1 (the
config dir is only readable by the controller's account). Keyed by device
id (the stable UUID the controller already uses everywhere). A shared
"zone" key for groups of identical fixtures is supported via a reserved
`zones:` mapping, but per-device keys are the recommended default.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ltp_controller.security import default_config_dir

logger = logging.getLogger(__name__)


class KeyStore:
    """Loads and persists per-device PSKs. PSKs are stored as hex."""

    def __init__(self, path: Path | None = None):
        self.path = path or (default_config_dir() / "keys.yaml")
        self._device_keys: dict[str, bytes] = {}
        self._zone_keys: dict[str, bytes] = {}
        self._device_zone: dict[str, str] = {}

    def load(self) -> None:
        if not self.path.exists():
            return
        data = yaml.safe_load(self.path.read_text()) or {}
        for device_id, hexkey in (data.get("devices") or {}).items():
            try:
                self._device_keys[str(device_id)] = bytes.fromhex(hexkey)
            except (ValueError, TypeError):
                logger.error(f"Keystore: invalid key for device {device_id}")
        for zone, hexkey in (data.get("zones") or {}).items():
            try:
                self._zone_keys[str(zone)] = bytes.fromhex(hexkey)
            except (ValueError, TypeError):
                logger.error(f"Keystore: invalid key for zone {zone}")
        self._device_zone = {str(d): str(z) for d, z in (data.get("device_zones") or {}).items()}
        logger.info(
            f"Keystore: loaded {len(self._device_keys)} device keys, "
            f"{len(self._zone_keys)} zone keys from {self.path}"
        )

    def save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "devices": {d: k.hex() for d, k in self._device_keys.items()},
        }
        if self._zone_keys:
            data["zones"] = {z: k.hex() for z, k in self._zone_keys.items()}
        if self._device_zone:
            data["device_zones"] = dict(self._device_zone)
        # Write 0600, atomically-ish (write then rename).
        tmp = self.path.with_suffix(".tmp")
        tmp.touch(mode=0o600)
        tmp.write_text(yaml.safe_dump(data, default_flow_style=False))
        tmp.replace(self.path)

    def get_key(self, device_id: str) -> bytes | None:
        """PSK for a device: its own key, else its zone key, else None."""
        if device_id in self._device_keys:
            return self._device_keys[device_id]
        zone = self._device_zone.get(device_id)
        if zone and zone in self._zone_keys:
            return self._zone_keys[zone]
        return None

    def has_key(self, device_id: str) -> bool:
        return self.get_key(device_id) is not None

    def set_key(self, device_id: str, key: bytes, persist: bool = True) -> None:
        if len(key) != 16:
            raise ValueError("PSK must be 16 bytes")
        self._device_keys[device_id] = key
        if persist:
            self.save()

    def remove_key(self, device_id: str, persist: bool = True) -> None:
        self._device_keys.pop(device_id, None)
        self._device_zone.pop(device_id, None)
        if persist:
            self.save()

    @property
    def device_ids(self) -> list[str]:
        return list(self._device_keys.keys())
