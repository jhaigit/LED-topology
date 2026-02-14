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


# Cache at import time — True if the platform supports IPv6 dual-stack
DUAL_STACK = socket.has_dualstack_ipv6()


def dual_stack_bind_address() -> str:
    """Return "::" when dual-stack is available, "0.0.0.0" otherwise."""
    return "::" if DUAL_STACK else "0.0.0.0"


def create_dual_stack_tcp_socket(host: str, port: int) -> socket.socket:
    """Create a TCP server socket, dual-stack if host is "::".

    Pre-configures IPV6_V6ONLY=0 to work around asyncio forcing it to 1.
    Returns a bound, listening, non-blocking socket ready for
    asyncio.start_server(sock=...).
    """
    family = address_family(host)
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind((host, port))
    sock.listen()
    sock.setblocking(False)
    return sock


def create_dual_stack_udp_socket(host: str, port: int) -> socket.socket:
    """Create a UDP socket, dual-stack if host is "::".

    Pre-configures IPV6_V6ONLY=0 to guarantee dual-stack regardless of
    net.ipv6.bindv6only sysctl. Returns a bound, non-blocking socket
    ready for create_datagram_endpoint(sock=...).
    """
    family = address_family(host)
    sock = socket.socket(family, socket.SOCK_DGRAM)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind((host, port))
    sock.setblocking(False)
    return sock


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
