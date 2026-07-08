"""Full-stack Layer 2: real controller pool claiming a real guarded sink.

Uses SerialSink in no_serial mode with a PSK as the device, and a real
SinkConnectionPool + KeyStore as the controller side. No mDNS: sinks are
injected into the controller registry directly, then connected via the
pool's normal connect path.
"""

import asyncio
import secrets

import pytest

from libltp.discovery import SERVICE_TYPE_SINK, DiscoveredDevice
from libltp.protocol import control_set
from libltp.types import ErrorCode, MessageType
from ltp_controller.controller import Controller, DeviceState
from ltp_controller.keystore import KeyStore
from ltp_controller.sink_connection_pool import SinkConnectionPool
from ltp_serial_sink.sink import SerialSink, SerialSinkConfig

PSK = secrets.token_bytes(16)


async def _make_sink(psk_hex: str, device_id: str) -> SerialSink:
    config = SerialSinkConfig(
        device_id=device_id,
        name="Auth Sink",
        no_serial=True,
        pixels=30,
        auth_psk=psk_hex,
    )
    sink = SerialSink(config)
    await sink.start()
    return sink


def _register_sink(controller: Controller, sink: SerialSink) -> DeviceState:
    """Inject a running sink into the controller registry as if discovered."""
    device = DiscoveredDevice(
        name="auth-sink",
        service_type=SERVICE_TYPE_SINK,
        host="127.0.0.1",
        port=sink.control_port,
        device_id=None,
        display_name="Auth Sink",
        description="",
        addresses=["127.0.0.1"],
        properties={"auth": "siphash", "id": str(sink.config.device_id)},
    )
    state = DeviceState(device=device)
    state._stable_id = str(sink.config.device_id)
    controller._sinks[device.name] = state
    return state


@pytest.fixture()
async def harness(monkeypatch):
    sink = await _make_sink(PSK.hex(), "11111111-1111-1111-1111-111111111111")
    controller = Controller(name="test-ctl")
    monkeypatch.setattr(controller, "_fetch_device_info", lambda s: asyncio.sleep(0))
    keystore = KeyStore()
    keystore.set_key(str(sink.config.device_id), PSK, persist=False)
    pool = SinkConnectionPool(controller, keystore=keystore)
    pool._running = True
    state = _register_sink(controller, sink)
    yield controller, pool, sink, state
    await pool.stop()
    await sink.stop()


class TestPoolClaimsSink:
    async def test_claim_and_signed_control(self, harness):
        controller, pool, sink, state = harness
        await pool._connect_to_sink(state)

        assert state.auth_state == "owned"
        assert sink._auth_guard.claimed

        # A control_set through the pool is signed and accepted
        response = await pool.request(state.id, control_set(0, {"brightness": 200}))
        assert response is not None
        assert response.type == MessageType.CONTROL_SET_RESPONSE

    async def test_missing_key_leaves_unkeyed(self, harness):
        controller, pool, sink, state = harness
        pool.keystore = KeyStore()  # empty — no key for this device
        await pool._connect_to_sink(state)
        assert state.auth_state == "unkeyed"
        assert not sink._auth_guard.claimed

        # Unsigned privileged request is rejected by the device
        response = await pool.request(state.id, control_set(0, {"brightness": 1}))
        assert response.type == MessageType.ERROR
        assert response.data["code"] == ErrorCode.UNAUTHORIZED

    async def test_second_controller_cannot_hijack(self, harness):
        controller, pool, sink, state = harness
        await pool._connect_to_sink(state)
        assert state.auth_state == "owned"

        # A second, independent controller with the same key
        other_ctl = Controller(name="ctl-2")
        other_pool = SinkConnectionPool(other_ctl, keystore=_keystore_with(sink, PSK))
        other_pool._running = True
        other_state = _register_sink(other_ctl, sink)
        try:
            await other_pool._connect_to_sink(other_state)
            # First owner still holds the lease; the newcomer is refused
            assert other_state.auth_state == "held"
            assert sink._auth_guard.owner_id == pool.controller_id
        finally:
            await other_pool.stop()

    async def test_release_on_disconnect_frees_device(self, harness):
        controller, pool, sink, state = harness
        await pool._connect_to_sink(state)
        assert sink._auth_guard.claimed
        await pool._disconnect_from_sink(state.id)
        await asyncio.sleep(0.05)
        assert not sink._auth_guard.claimed  # graceful release freed the lease


def _keystore_with(sink: SerialSink, psk: bytes) -> KeyStore:
    ks = KeyStore()
    ks.set_key(str(sink.config.device_id), psk, persist=False)
    return ks
