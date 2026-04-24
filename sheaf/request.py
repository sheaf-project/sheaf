"""Request utilities shared across the application."""

from ipaddress import ip_address

from fastapi import Request

from sheaf.config import settings


def client_ip(request: Request) -> str:
    """Extract the real client IP from a request.

    Only reads X-Forwarded-For when the direct connection comes from a peer
    matching TRUSTED_PROXIES (exact IPs or CIDR ranges). This prevents clients
    from spoofing their IP by sending a fake X-Forwarded-For header when
    connecting directly.
    """
    direct_ip = request.client.host if request.client else "unknown"

    if settings.trusted_proxies:
        try:
            direct_addr = ip_address(direct_ip)
        except ValueError:
            # direct_ip is "unknown" or otherwise unparseable — don't trust XFF.
            return direct_ip
        if any(direct_addr in net for net in settings.trusted_proxy_networks):
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()

    return direct_ip
