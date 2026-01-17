"""Art-Net Protocol Implementation.

Art-Net 4 packet structures and builders.
Reference: https://art-net.org.uk/downloads/art-net.pdf
"""

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import NamedTuple

# Art-Net constants
ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
ARTNET_VERSION = 14  # Protocol version


class OpCode(IntEnum):
    """Art-Net operation codes (little-endian in packets)."""

    POLL = 0x2000
    POLL_REPLY = 0x2100
    DIAG_DATA = 0x2300
    COMMAND = 0x2400
    DMX = 0x5000
    NZS = 0x5100
    SYNC = 0x5200
    ADDRESS = 0x6000
    INPUT = 0x7000
    TOD_REQUEST = 0x8000
    TOD_DATA = 0x8100
    TOD_CONTROL = 0x8200
    RDM = 0x8300
    RDM_SUB = 0x8400
    VIDEO_SETUP = 0xA010
    VIDEO_PALETTE = 0xA020
    VIDEO_DATA = 0xA040
    MAC_MASTER = 0xF000
    MAC_SLAVE = 0xF100
    FIRMWARE_MASTER = 0xF200
    FIRMWARE_REPLY = 0xF300
    FILE_TN_MASTER = 0xF400
    FILE_FN_MASTER = 0xF500
    FILE_FN_REPLY = 0xF600
    IP_PROG = 0xF800
    IP_PROG_REPLY = 0xF900
    MEDIA = 0x9000
    MEDIA_PATCH = 0x9100
    MEDIA_CONTROL = 0x9200
    MEDIA_CONTROL_REPLY = 0x9300
    TIME_CODE = 0x9700
    TIME_SYNC = 0x9800
    TRIGGER = 0x9900
    DIRECTORY = 0x9A00
    DIRECTORY_REPLY = 0x9B00


class PortType(IntEnum):
    """Port type flags for ArtPollReply."""

    DMX512 = 0x00
    MIDI = 0x01
    AVAB = 0x02
    COLORTRAN_CMX = 0x03
    ADB_625 = 0x04
    ARTNET = 0x05
    DALI = 0x06


@dataclass
class ArtDmxPacket:
    """ArtDmx packet (OpCode 0x5000).

    Contains DMX512 channel data for a single universe.
    """

    sequence: int  # 0 = disabled, 1-255 wrapping sequence
    physical: int  # Physical input port (informational)
    universe: int  # 15-bit port-address (Net:SubNet:Universe)
    data: bytes  # DMX channel data (2-512 bytes, even length)

    @property
    def net(self) -> int:
        """Extract Net from universe (bits 14-8)."""
        return (self.universe >> 8) & 0x7F

    @property
    def subnet(self) -> int:
        """Extract SubNet from universe (bits 7-4)."""
        return (self.universe >> 4) & 0x0F

    @property
    def uni(self) -> int:
        """Extract Universe from universe (bits 3-0)."""
        return self.universe & 0x0F


@dataclass
class ArtPollPacket:
    """ArtPoll packet (OpCode 0x2000).

    Discovery request broadcast by controllers.
    """

    talk_to_me: int = 0  # Flags for response behavior
    priority: int = 0  # Minimum diagnostic priority


@dataclass
class ArtPollReplyPacket:
    """ArtPollReply packet (OpCode 0x2100).

    Node announcement in response to ArtPoll.
    """

    ip_address: tuple[int, int, int, int]
    port: int
    version: int
    net_switch: int
    sub_switch: int
    oem: int
    ubea_version: int
    status1: int
    esta_code: int
    short_name: str  # 18 chars max
    long_name: str  # 64 chars max
    node_report: str  # 64 chars max
    num_ports: int
    port_types: bytes  # 4 bytes
    good_input: bytes  # 4 bytes
    good_output: bytes  # 4 bytes
    sw_in: bytes  # 4 bytes (universe for each input)
    sw_out: bytes  # 4 bytes (universe for each output)
    style: int
    mac_address: bytes  # 6 bytes
    bind_ip: tuple[int, int, int, int]
    bind_index: int
    status2: int


class UniverseAddress(NamedTuple):
    """15-bit port-address broken into components."""

    net: int  # 0-127
    subnet: int  # 0-15
    universe: int  # 0-15

    def to_int(self) -> int:
        """Convert to 15-bit integer."""
        return ((self.net & 0x7F) << 8) | ((self.subnet & 0x0F) << 4) | (self.universe & 0x0F)

    @classmethod
    def from_int(cls, value: int) -> "UniverseAddress":
        """Parse from 15-bit integer."""
        return cls(
            net=(value >> 8) & 0x7F,
            subnet=(value >> 4) & 0x0F,
            universe=value & 0x0F,
        )


def build_artdmx(
    universe: int,
    data: bytes,
    sequence: int = 0,
    physical: int = 0,
) -> bytes:
    """Build an ArtDmx packet.

    Args:
        universe: 15-bit port-address (0-32767)
        data: DMX channel data (1-512 bytes)
        sequence: Sequence number (0=disabled, 1-255)
        physical: Physical port number (informational)

    Returns:
        Complete Art-Net packet ready for UDP transmission
    """
    # Ensure even length (Art-Net requirement)
    if len(data) % 2 != 0:
        data = data + b"\x00"

    # Clamp to 512 channels
    if len(data) > 512:
        data = data[:512]

    length = len(data)

    # Build packet:
    # Header: "Art-Net\0" (8 bytes)
    # OpCode: 0x5000 (2 bytes, little-endian)
    # ProtVer: 14 (2 bytes, big-endian)
    # Sequence: 1 byte
    # Physical: 1 byte
    # SubUni: universe low byte
    # Net: universe high byte (bits 14-8)
    # LengthHi, LengthLo: 2 bytes big-endian
    # Data: DMX channels

    packet = bytearray(18 + length)
    packet[0:8] = ARTNET_HEADER
    struct.pack_into("<H", packet, 8, OpCode.DMX)  # OpCode little-endian
    struct.pack_into(">H", packet, 10, ARTNET_VERSION)  # ProtVer big-endian
    packet[12] = sequence & 0xFF
    packet[13] = physical & 0xFF
    packet[14] = universe & 0xFF  # SubUni (low byte)
    packet[15] = (universe >> 8) & 0x7F  # Net (high 7 bits)
    struct.pack_into(">H", packet, 16, length)  # Length big-endian
    packet[18:] = data

    return bytes(packet)


def build_artpoll(talk_to_me: int = 0x02, priority: int = 0) -> bytes:
    """Build an ArtPoll packet.

    Args:
        talk_to_me: Response behavior flags (0x02 = reply on change)
        priority: Minimum diagnostic priority level

    Returns:
        Complete Art-Net packet ready for UDP broadcast
    """
    packet = bytearray(14)
    packet[0:8] = ARTNET_HEADER
    struct.pack_into("<H", packet, 8, OpCode.POLL)
    struct.pack_into(">H", packet, 10, ARTNET_VERSION)
    packet[12] = talk_to_me
    packet[13] = priority

    return bytes(packet)


def build_artpoll_reply(
    ip_address: tuple[int, int, int, int],
    short_name: str,
    long_name: str,
    universes: list[int],
    port: int = ARTNET_PORT,
    mac_address: bytes = b"\x00\x00\x00\x00\x00\x00",
    esta_code: int = 0x0000,
    oem_code: int = 0x0000,
) -> bytes:
    """Build an ArtPollReply packet.

    Args:
        ip_address: Node IP address
        short_name: Short name (max 18 chars)
        long_name: Long name (max 64 chars)
        universes: List of output universes
        port: Art-Net port number
        mac_address: MAC address (6 bytes)
        esta_code: ESTA manufacturer code
        oem_code: OEM code

    Returns:
        Complete Art-Net packet
    """
    packet = bytearray(239)  # Fixed size for ArtPollReply
    packet[0:8] = ARTNET_HEADER
    struct.pack_into("<H", packet, 8, OpCode.POLL_REPLY)

    # IP address (4 bytes)
    packet[10:14] = bytes(ip_address)

    # Port (2 bytes, little-endian)
    struct.pack_into("<H", packet, 14, port)

    # Version (2 bytes, big-endian)
    struct.pack_into(">H", packet, 16, 0x0001)  # Firmware version

    # Net/SubSwitch
    if universes:
        first_uni = UniverseAddress.from_int(universes[0])
        packet[18] = first_uni.net
        packet[19] = first_uni.subnet
    else:
        packet[18] = 0
        packet[19] = 0

    # OEM code (2 bytes, big-endian)
    struct.pack_into(">H", packet, 20, oem_code)

    # UBEA version
    packet[22] = 0

    # Status1
    packet[23] = 0xD0  # Ready, supports RDM

    # ESTA code (2 bytes, little-endian)
    struct.pack_into("<H", packet, 24, esta_code)

    # Short name (18 bytes, null-padded)
    short_bytes = short_name.encode("ascii", errors="replace")[:17]
    packet[26 : 26 + len(short_bytes)] = short_bytes

    # Long name (64 bytes, null-padded)
    long_bytes = long_name.encode("ascii", errors="replace")[:63]
    packet[44 : 44 + len(long_bytes)] = long_bytes

    # Node report (64 bytes)
    report = "#0001 [0000] LTP Art-Net Sink"
    report_bytes = report.encode("ascii")[:63]
    packet[108 : 108 + len(report_bytes)] = report_bytes

    # NumPorts (2 bytes, big-endian) - up to 4 output ports
    num_ports = min(len(universes), 4)
    struct.pack_into(">H", packet, 172, num_ports)

    # Port types (4 bytes) - all outputs, DMX512 type
    for i in range(4):
        if i < num_ports:
            packet[174 + i] = 0x80 | PortType.DMX512  # Output + DMX512
        else:
            packet[174 + i] = 0x00

    # GoodInput (4 bytes) - not used for outputs
    packet[178:182] = b"\x00\x00\x00\x00"

    # GoodOutput (4 bytes) - data being transmitted
    for i in range(4):
        if i < num_ports:
            packet[182 + i] = 0x80  # Data transmitted
        else:
            packet[182 + i] = 0x00

    # SwIn (4 bytes) - input universe
    packet[186:190] = b"\x00\x00\x00\x00"

    # SwOut (4 bytes) - output universe (low nibble)
    for i in range(4):
        if i < len(universes):
            packet[190 + i] = universes[i] & 0x0F

    # SwVideo, SwMacro, SwRemote - not used
    packet[194] = 0
    packet[195] = 0
    packet[196] = 0

    # Spare (3 bytes)
    packet[197:200] = b"\x00\x00\x00"

    # Style: StNode (0x00)
    packet[200] = 0x00

    # MAC address (6 bytes)
    packet[201:207] = mac_address[:6].ljust(6, b"\x00")

    # BindIp (4 bytes) - same as IP
    packet[207:211] = bytes(ip_address)

    # BindIndex
    packet[211] = 1

    # Status2
    packet[212] = 0x08  # Supports 15-bit port-address

    return bytes(packet)


def build_artsync() -> bytes:
    """Build an ArtSync packet.

    Used to synchronize outputs across multiple universes.
    """
    packet = bytearray(14)
    packet[0:8] = ARTNET_HEADER
    struct.pack_into("<H", packet, 8, OpCode.SYNC)
    struct.pack_into(">H", packet, 10, ARTNET_VERSION)
    # Aux1, Aux2 reserved
    packet[12] = 0
    packet[13] = 0

    return bytes(packet)


def parse_artnet_packet(data: bytes) -> ArtDmxPacket | ArtPollPacket | None:
    """Parse an incoming Art-Net packet.

    Args:
        data: Raw UDP packet data

    Returns:
        Parsed packet object or None if invalid/unsupported
    """
    if len(data) < 12:
        return None

    # Check header
    if data[0:8] != ARTNET_HEADER:
        return None

    # Get OpCode (little-endian)
    opcode = struct.unpack_from("<H", data, 8)[0]

    if opcode == OpCode.DMX:
        if len(data) < 18:
            return None

        sequence = data[12]
        physical = data[13]
        universe = data[14] | (data[15] << 8)
        length = struct.unpack_from(">H", data, 16)[0]

        if len(data) < 18 + length:
            return None

        return ArtDmxPacket(
            sequence=sequence,
            physical=physical,
            universe=universe,
            data=bytes(data[18 : 18 + length]),
        )

    elif opcode == OpCode.POLL:
        talk_to_me = data[12] if len(data) > 12 else 0
        priority = data[13] if len(data) > 13 else 0
        return ArtPollPacket(talk_to_me=talk_to_me, priority=priority)

    return None


def pixels_to_universes(pixel_count: int, bytes_per_pixel: int = 3) -> int:
    """Calculate number of universes needed for given pixel count.

    Args:
        pixel_count: Total number of pixels
        bytes_per_pixel: 3 for RGB, 4 for RGBW

    Returns:
        Number of universes required
    """
    channels = pixel_count * bytes_per_pixel
    return (channels + 511) // 512


def universe_pixel_range(
    universe_index: int, bytes_per_pixel: int = 3
) -> tuple[int, int]:
    """Get pixel range for a universe.

    Args:
        universe_index: 0-based universe index
        bytes_per_pixel: 3 for RGB, 4 for RGBW

    Returns:
        (start_pixel, end_pixel) tuple (exclusive end)
    """
    pixels_per_universe = 512 // bytes_per_pixel
    start = universe_index * pixels_per_universe
    end = start + pixels_per_universe
    return (start, end)
