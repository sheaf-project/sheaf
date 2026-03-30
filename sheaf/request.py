"""Request utilities shared across the application."""

from fastapi import Request

from sheaf.config import settings


def client_ip(request: Request) -> str:
    """Extract the real client IP from a request.

    Only reads X-Forwarded-For when the direct connection comes from an IP
    listed in TRUSTED_PROXIES. This prevents clients from spoofing their IP
    by sending a fake X-Forwarded-For header when connecting directly.
    """
    direct_ip = request.client.host if request.client else "unknown"

    if settings.trusted_proxies and direct_ip in settings.trusted_proxy_set:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()

    return direct_ip
