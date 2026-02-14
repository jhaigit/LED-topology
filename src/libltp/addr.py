"""Address utilities for IPv4/IPv6 dual-stack support."""

import socket


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


def dual_stack_bind_address() -> str:
    """Return the wildcard bind address for servers.

    Returns "0.0.0.0" (IPv4). CPython's asyncio.start_server explicitly
    sets IPV6_V6ONLY=1 on IPv6 sockets, so binding to "::" only accepts
    IPv6 connections — IPv4 clients get ECONNREFUSED. Until we pre-create
    sockets with IPV6_V6ONLY=0, IPv4 is the safe default.
    """
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
