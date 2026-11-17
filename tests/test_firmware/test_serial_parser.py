"""Tests for the firmware-side LTP serial packet parser (arduino/libraries/LtpProtocol).

The parser is shared by every serial sketch. These cover the opt-in handling
of packets longer than the receive buffer, which the pattern-only firmware
(ltp_stream_serial) turns on so that pixel frames sent to it are skipped
whole, and confirm that the default behaviour other sketches rely on is
unchanged.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "arduino" / "libraries" / "LtpProtocol" / "src"
HERE = Path(__file__).parent

CMD_NOP, CMD_PIXEL_FRAME, CMD_SET_CONTROL = 0x00, 0x33, 0x40
BUFFER = 32  # MAX_PAYLOAD_SIZE of ltp_stream_serial

pytestmark = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    exe = tmp_path_factory.mktemp("proto") / "protocol_harness"
    subprocess.run(
        ["g++", "-std=gnu++11", "-O1", "-Wall", "-Wextra",
         f"-I{HERE / 'arduino_stub'}", f"-I{LIB}",
         str(HERE / "protocol_harness.cpp"), str(LIB / "ltp_protocol.cpp"), "-o", str(exe)],
        check=True,
    )
    return exe


def packet(cmd, payload=b"", flags=0):
    body = bytes([flags, len(payload) & 0xFF, len(payload) >> 8, cmd]) + bytes(payload)
    checksum = 0
    for b in body:
        checksum ^= b
    return b"\xaa" + body + bytes([checksum])


def parse(harness, data, skip_oversize):
    out = subprocess.run([str(harness), str(BUFFER), "1" if skip_oversize else "0"],
                         input=data, capture_output=True, check=True).stdout.decode()
    packets = []
    for line in out.splitlines():
        fields = dict(part.split("=") for part in line.split())
        packets.append((int(fields["cmd"], 16), int(fields["length"]),
                        fields["oversize"] == "1", bytes.fromhex(fields["payload"])))
    return packets


def test_small_packets_parse_the_same_either_way(harness):
    data = packet(CMD_SET_CONTROL, [0, 200]) + packet(CMD_NOP)
    expected = [(CMD_SET_CONTROL, 2, False, bytes([0, 200])), (CMD_NOP, 0, False, b"")]
    assert parse(harness, data, skip_oversize=False) == expected
    assert parse(harness, data, skip_oversize=True) == expected


def test_oversize_packet_is_skipped_whole(harness):
    """A pixel frame is returned as one oversize packet and nothing in it is acted on."""
    # The payload is itself a valid SET_CONTROL packet, repeated: exactly what
    # must not be mistaken for commands.
    inner = packet(CMD_SET_CONTROL, [6, 3]) * 40
    data = packet(CMD_PIXEL_FRAME, inner) + packet(CMD_SET_CONTROL, [0, 99])
    assert parse(harness, data, skip_oversize=True) == [
        (CMD_PIXEL_FRAME, len(inner), True, b""),
        (CMD_SET_CONTROL, 2, False, bytes([0, 99])),
    ]


def test_oversize_packet_with_bad_checksum_is_dropped(harness):
    frame = bytearray(packet(CMD_PIXEL_FRAME, bytes(range(200))))
    frame[-1] ^= 0x55
    data = bytes(frame) + packet(CMD_NOP)
    assert parse(harness, data, skip_oversize=True) == [(CMD_NOP, 0, False, b"")]


def test_packet_that_exactly_fills_the_buffer_is_not_oversize(harness):
    payload = bytes(range(BUFFER))
    assert parse(harness, packet(CMD_PIXEL_FRAME, payload), skip_oversize=True) == [
        (CMD_PIXEL_FRAME, BUFFER, False, payload)]


def test_default_still_resynchronises_inside_an_oversize_packet(harness):
    """Without the option the parser behaves as it always has: it gives up on
    the long packet and hunts for the next start byte, here finding the
    commands embedded in the payload."""
    inner = packet(CMD_SET_CONTROL, [6, 3]) * 40
    packets = parse(harness, packet(CMD_PIXEL_FRAME, inner), skip_oversize=False)
    assert packets and all(p[0] == CMD_SET_CONTROL and not p[2] for p in packets)
