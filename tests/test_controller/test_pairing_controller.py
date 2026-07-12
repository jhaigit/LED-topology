"""Controller-side X25519+PIN pairing orchestration (Phase 4b).

Drives SinkConnectionPool.pair_device against a ReferenceDevice (the Python
twin of the ESP32 firmware role) relayed through a faked transport, so the
full ControllerPairing <-> device handshake and keystore persistence are
exercised without hardware or sockets.
"""

from types import SimpleNamespace

import pytest

from libltp.pairing import PairingError, ReferenceDevice
from libltp.protocol import Message
from libltp.types import ErrorCode, MessageType
from ltp_controller.keystore import KeyStore
from ltp_controller.sink_connection_pool import SinkConnectionPool

SINK_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _pool(tmp_path, device: ReferenceDevice, *, arm: bool = True):
    controller = SimpleNamespace(
        device_id="test-controller",
        get_sink=lambda sid: (
            SimpleNamespace(id=SINK_ID, online=True) if sid == SINK_ID else None
        ),
    )
    keystore = KeyStore(path=tmp_path / "keys.yaml")
    pool = SinkConnectionPool(controller, keystore=keystore)
    pool._running = True

    async def fake_request(sink_id, msg, timeout=5.0):
        if not arm:
            return Message(
                MessageType.ERROR, seq=msg.seq,
                code=int(ErrorCode.NOT_PAIRING), message="not in pairing mode",
            )
        if msg.type == MessageType.PAIR_BEGIN:
            resp = device.on_begin(msg.data)
            return Message(MessageType.PAIR_BEGIN_RESPONSE, seq=msg.seq, **resp)
        if msg.type == MessageType.PAIR_CONFIRM:
            try:
                resp = device.on_confirm(msg.data)
            except PairingError as e:
                return Message(
                    MessageType.ERROR, seq=msg.seq,
                    code=int(ErrorCode.PAIR_FAILED), message=str(e),
                )
            return Message(MessageType.PAIR_COMPLETE, seq=msg.seq, **resp)
        raise AssertionError(f"unexpected message {msg.type}")

    async def noop_reclaim(sink_id):
        return None

    pool.request = fake_request  # type: ignore[assignment]
    pool.reclaim = noop_reclaim  # type: ignore[assignment]
    return pool, keystore


async def test_pair_happy_path(tmp_path):
    device = ReferenceDevice()
    pool, keystore = _pool(tmp_path, device)
    ok, msg = await pool.pair_device(SINK_ID, device.pin)
    assert ok, msg
    # Both sides derived the SAME key, and it was persisted.
    assert keystore.get_key(SINK_ID) == device.psk


async def test_pair_wrong_pin(tmp_path):
    device = ReferenceDevice(pin="11112222")
    pool, keystore = _pool(tmp_path, device)
    ok, msg = await pool.pair_device(SINK_ID, "99998888")
    assert not ok
    assert "reject" in msg.lower() or "fail" in msg.lower()
    assert not keystore.has_key(SINK_ID)


async def test_pair_device_not_armed(tmp_path):
    device = ReferenceDevice()
    pool, keystore = _pool(tmp_path, device, arm=False)
    ok, msg = await pool.pair_device(SINK_ID, device.pin)
    assert not ok
    assert not keystore.has_key(SINK_ID)


async def test_pair_offline_device(tmp_path):
    device = ReferenceDevice()
    pool, keystore = _pool(tmp_path, device)
    ok, msg = await pool.pair_device("unknown-id", device.pin)
    assert not ok and "offline" in msg
