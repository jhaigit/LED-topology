"""Tests for the Layer 2 device keystore."""

import secrets

import pytest

from ltp_controller.keystore import KeyStore


class TestKeyStore:
    def test_set_get_roundtrip_persists(self, tmp_path):
        path = tmp_path / "keys.yaml"
        ks = KeyStore(path)
        key = secrets.token_bytes(16)
        ks.set_key("dev-1", key)
        assert path.exists()
        assert oct(path.stat().st_mode & 0o777) == "0o600"

        # A fresh instance reads it back
        ks2 = KeyStore(path)
        ks2.load()
        assert ks2.get_key("dev-1") == key
        assert ks2.has_key("dev-1")

    def test_missing_device(self, tmp_path):
        ks = KeyStore(tmp_path / "keys.yaml")
        assert ks.get_key("nope") is None
        assert not ks.has_key("nope")

    def test_rejects_wrong_length(self, tmp_path):
        ks = KeyStore(tmp_path / "keys.yaml")
        with pytest.raises(ValueError):
            ks.set_key("dev-1", b"too-short")

    def test_zone_key_fallback(self, tmp_path):
        path = tmp_path / "keys.yaml"
        path.write_text(
            "zones:\n"
            "  stage: " + secrets.token_bytes(16).hex() + "\n"
            "device_zones:\n"
            "  dev-9: stage\n"
        )
        ks = KeyStore(path)
        ks.load()
        assert ks.has_key("dev-9")  # via zone
        assert not ks.has_key("dev-other")

    def test_device_key_overrides_zone(self, tmp_path):
        path = tmp_path / "keys.yaml"
        dev_key = secrets.token_bytes(16)
        ks = KeyStore(path)
        ks.set_key("dev-9", dev_key, persist=False)
        ks._zone_keys["stage"] = secrets.token_bytes(16)
        ks._device_zone["dev-9"] = "stage"
        assert ks.get_key("dev-9") == dev_key  # own key wins

    def test_remove(self, tmp_path):
        ks = KeyStore(tmp_path / "keys.yaml")
        ks.set_key("dev-1", secrets.token_bytes(16))
        ks.remove_key("dev-1")
        assert not ks.has_key("dev-1")

    def test_corrupt_key_skipped(self, tmp_path):
        path = tmp_path / "keys.yaml"
        path.write_text("devices:\n  dev-1: not-hex\n  dev-2: " + secrets.token_bytes(16).hex())
        ks = KeyStore(path)
        ks.load()
        assert not ks.has_key("dev-1")
        assert ks.has_key("dev-2")
