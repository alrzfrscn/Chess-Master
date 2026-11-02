"""Find the laptop's primary LAN IPv4 address for multi-device play."""

from __future__ import annotations

import socket

__all__ = ["local_ipv4"]


def local_ipv4() -> str | None:
    """Best-effort primary LAN address, or ``None`` when the machine is offline."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.4)
            # A connected UDP socket picks the outbound interface without
            # sending a packet.
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
        if address and not address.startswith("127.") and address != "0.0.0.0":
            return address
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return None
