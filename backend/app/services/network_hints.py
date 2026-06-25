"""Network connection hints for the companion app.

Port of src/server/network-hints.ts. Pure logic over the host's network
interfaces; classifies IPv4 addresses into tailscale / lan and always appends a
local hint, sorted by kind weight.
"""

import socket
from typing import Literal

ConnectionHintKind = Literal["tailscale", "lan", "local"]


def _enumerate_ipv4() -> list[tuple[str, str]]:
    """Return (interface_name, ipv4_address) pairs for non-loopback IPv4 addrs.

    Python's stdlib has no portable ``networkInterfaces()`` equivalent, so we use
    ``getaddrinfo`` on the host name plus the resolved local hostname address.
    Loopback addresses are filtered out by the caller via classification.
    """
    addrs: set[str] = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, family=socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return [("", a) for a in sorted(addrs)]


def _classify_ip(ip: str) -> ConnectionHintKind | None:
    parts_raw = ip.split(".")
    if len(parts_raw) != 4:
        return None
    try:
        parts = [int(p) for p in parts_raw]
    except ValueError:
        return None
    if any(p < 0 or p > 255 for p in parts):
        return None

    first, second = parts[0], parts[1]
    if first == 100 and 64 <= second <= 127:
        return "tailscale"
    if first == 10:
        return "lan"
    if first == 172 and 16 <= second <= 31:
        return "lan"
    if first == 192 and second == 168:
        return "lan"
    return None


def _with_port(hostname: str, port: str) -> str:
    return f"{hostname}:{port}" if port else hostname


def _kind_weight(kind: ConnectionHintKind) -> int:
    return {"tailscale": 0, "lan": 1, "local": 2}[kind]


def get_connection_hints(
    *, protocol: str, remote_port: str, local_port: str
) -> list[dict]:
    """Build connection hints (camelCase dicts) for the companion app."""
    hints: list[dict] = []
    seen: set[str] = set()

    for interface_name, address in _enumerate_ipv4():
        kind = _classify_ip(address)
        if kind is None:
            continue
        host = _with_port(address, remote_port)
        url = f"{protocol}//{host}"
        if url in seen:
            continue
        seen.add(url)
        hint: dict = {
            "kind": kind,
            "label": "Tailscale" if kind == "tailscale" else "局域网",
            "host": host,
            "url": url,
        }
        if interface_name:
            hint["interfaceName"] = interface_name
        hints.append(hint)

    local_host = _with_port("localhost", local_port)
    hints.append(
        {
            "kind": "local",
            "label": "本机预览",
            "host": local_host,
            "url": f"{protocol}//{local_host}",
        }
    )

    hints.sort(key=lambda h: _kind_weight(h["kind"]))
    return hints
