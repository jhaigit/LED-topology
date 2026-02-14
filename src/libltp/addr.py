"""Address utilities for IPv4/IPv6 dual-stack support."""

import socket
import sys


def is_ipv6(address: str) -> bool:
    """Check if a string is an IPv6 address.

    Handles bracketed form [::1], bare form ::1, and scope IDs (fe80::1%eth0).
    """
    addr = address.strip("[]")
    if "%" in addr:
        addr = addr.split("%")[0]
    try:
        socket.inet_pton(socket.AF_INET6, addr)
        return True
    except (OSError, ValueError):
        return False


def is_ipv4(address: str) -> bool:
    """Check if a string is an IPv4 address."""
    try:
        socket.inet_pton(socket.AF_INET, address)
        return True
    except (OSError, ValueError):
        return False


def address_family(address: str) -> socket.AddressFamily:
    """Determine the socket address family for a given address string."""
    if is_ipv6(address):
        return socket.AF_INET6
    return socket.AF_INET


def normalize_ipv6(address: str) -> str:
    """Strip brackets from an IPv6 address if present.

    "[::1]" -> "::1", "::1" -> "::1", "192.168.1.1" -> "192.168.1.1"
    """
    if address.startswith("[") and "]" in address:
        return address[1:address.index("]")]
    return address


def format_address_port(host: str, port: int) -> str:
    """Format host:port correctly for both IPv4 and IPv6.

    IPv4: "192.168.1.1:8080"
    IPv6: "[::1]:8080"
    """
    if is_ipv6(host):
        bare = normalize_ipv6(host)
        return f"[{bare}]:{port}"
    return f"{host}:{port}"


def _can_bind_dual_stack() -> bool:
    """Test whether :: sockets accept IPv4 by default.

    asyncio.start_server and create_datagram_endpoint do NOT set
    IPV6_V6ONLY, so we must check the kernel default (net.ipv6.bindv6only).
    If it's 1, :: sockets only accept IPv6 and IPv4 connections are refused.
    """
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        v6only = sock.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY)
        sock.close()
        return v6only == 0
    except (OSError, AttributeError):
        return False


# Cache the result at import time so we don't probe on every call
_DUAL_STACK_AVAILABLE: bool = sys.platform == "linux" and _can_bind_dual_stack()


def dual_stack_bind_address() -> str:
    """Return the appropriate wildcard bind address for dual-stack.

    Returns "::" on Linux when IPv6 dual-stack is confirmed to work
    (IPv6 enabled and IPV6_V6ONLY can be set to 0). Falls back to
    "0.0.0.0" otherwise.
    """
    if _DUAL_STACK_AVAILABLE:
        return "::"
    return "0.0.0.0"


def get_local_ip(remote_host: str) -> str:
    """Get the local IP address that can reach the given remote host.

    Determines address family from the remote host and uses a UDP
    connect trick to find the correct source address.
    """
    family = address_family(remote_host)
    bare_host = normalize_ipv6(remote_host)

    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.settimeout(0)
        sock.connect((bare_host, 1))
        local_ip = sock.getsockname()[0]
        sock.close()
        return local_ip
    except Exception:
        try:
            if family == socket.AF_INET6:
                return "::1"
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "::1" if family == socket.AF_INET6 else "127.0.0.1"
