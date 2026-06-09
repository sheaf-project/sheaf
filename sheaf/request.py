"""Request utilities shared across the application."""

from ipaddress import ip_address

from fastapi import Request

from sheaf.config import settings


def client_ip(request: Request) -> str:
    """Extract the real client IP from a request.

    Only reads X-Forwarded-For when the direct connection comes from a peer
    matching TRUSTED_PROXIES (exact IPs or CIDR ranges), and then walks the
    header RIGHT to LEFT, returning the first entry that is not itself a
    trusted proxy.

    Proxies (including the shipped nginx templates, via
    $proxy_add_x_forwarded_for) APPEND the peer they saw to whatever the
    client sent, so the leftmost entries are attacker-controlled: taking
    XFF[0] let any client rotate a fake IP per request and walk straight
    through every per-IP rate limit, and poisoned signup/session/audit IP
    records. The rightmost non-proxy entry is the first hop the client
    could not have forged.
    """
    direct_ip = request.client.host if request.client else "unknown"

    if not settings.trusted_proxies:
        return direct_ip
    try:
        direct_addr = ip_address(direct_ip)
    except ValueError:
        # direct_ip is "unknown" or otherwise unparseable — don't trust XFF.
        return direct_ip
    nets = settings.trusted_proxy_networks
    if not any(direct_addr in net for net in nets):
        return direct_ip

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return direct_ip

    entries = [e.strip() for e in forwarded.split(",") if e.strip()]
    for entry in reversed(entries):
        try:
            addr = ip_address(entry)
        except ValueError:
            # Garbage in the chain. Everything left of a malformed entry
            # is client-controlled by definition, so stop here and fall
            # back to the direct peer rather than guessing.
            return direct_ip
        if not any(addr in net for net in nets):
            return entry

    # Every entry was a trusted proxy (e.g. proxy-to-proxy health checks
    # with no external client in the chain): use the direct peer.
    return direct_ip
