from __future__ import annotations

import ipaddress
import socket


def discover_lan_ipv4() -> str | None:
    """Returns the IPv4 address selected by the active network route."""
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("1.1.1.1", 53))
            candidates.append(str(probe.getsockname()[0]))
    except OSError:
        pass
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    fallback: str | None = None
    for value in dict.fromkeys(candidates):
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if not isinstance(address, ipaddress.IPv4Address) or address.is_loopback:
            continue
        if address.is_private and not address.is_link_local:
            return value
        fallback = fallback or value
    return fallback


def lan_url(port: int) -> str | None:
    address = discover_lan_ipv4()
    return f"http://{address}:{port}" if address else None
