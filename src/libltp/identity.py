"""Persistent static X25519 identity for a controller or a serial-sink fleet.

The keypair is the peer's long-lived identity used for fleet enrollment
(see fleet_enroll.py and docs/proposals/fleet-enrollment.md). It is stored as
a single hex line in a `0600` file under the XDG config dir so it survives
restarts; the fingerprint is the value an admin compares out-of-band.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from libltp.fleet_enroll import fingerprint, generate_identity

logger = logging.getLogger(__name__)


def config_dir() -> Path:
    """~/.config/ltp, honoring $XDG_CONFIG_HOME (matches the controller)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ltp"


class Identity:
    """A load-or-create static X25519 identity persisted at `path`."""

    def __init__(self, private_key: bytes, public_key: bytes, path: Path):
        self.private_key = private_key
        self.public_key = public_key
        self.path = path

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.public_key)

    @classmethod
    def load_or_create(cls, path: Path | None = None, name: str = "fleet-identity") -> "Identity":
        p = path or (config_dir() / name)
        if p.exists():
            try:
                priv = bytes.fromhex(p.read_text().strip())
                if len(priv) != 32:
                    raise ValueError(f"expected 32-byte key, got {len(priv)}")
                # Derive the public key from the stored private key.
                from cryptography.hazmat.primitives.asymmetric.x25519 import (
                    X25519PrivateKey,
                )
                from cryptography.hazmat.primitives.serialization import (
                    Encoding,
                    PublicFormat,
                )

                pub = (
                    X25519PrivateKey.from_private_bytes(priv)
                    .public_key()
                    .public_bytes(Encoding.Raw, PublicFormat.Raw)
                )
                ident = cls(priv, pub, p)
                logger.info(f"Loaded identity {ident.fingerprint} from {p}")
                return ident
            except (ValueError, OSError) as exc:
                logger.error(f"Identity file {p} unreadable ({exc}); regenerating")
        priv, pub = generate_identity()
        ident = cls(priv, pub, p)
        ident.save()
        logger.info(f"Generated new identity {ident.fingerprint} at {p}")
        return ident

    def save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Write 0600 without a window where the key is world-readable.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (self.private_key.hex() + "\n").encode("ascii"))
        finally:
            os.close(fd)
        os.chmod(self.path, 0o600)
