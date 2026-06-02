"""Shared histogram bucket definitions.

Keeping these in one place stops bucket drift across metric definitions
and makes it obvious which bucket family a new metric should use.
"""

# HTTP request latency. Default range fits FastAPI handlers comfortably:
# fast cached reads in the 5-20ms range, P99 typical paths well under a
# second, with headroom for the rare slow endpoint.
HTTP_LATENCY_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

# Outbound dispatch latency (email send, notification dispatch, webhook
# delivery, job duration). Starts later than HTTP because none of these
# paths are realistically sub-50ms, extends further to cover slow
# external providers.
DISPATCH_LATENCY_BUCKETS = (
    0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0,
)

# Request-rate distribution buckets: requests-per-minute per identifier
# (per-IP or per-account). The histogram captures "how hard the busiest
# identifiers are hitting us" without ever putting the identifier into
# a label. Buckets cover everything from "normal browsing" through
# "credential-stuffing botnet".
RATE_DISTRIBUTION_BUCKETS = (
    1, 5, 10, 30, 60, 120, 300, 600, 1800,
)

# Export size, in bytes. Tier-bucket coverage from "tiny system, JSON
# only" through "large system with images, multi-GB tarball".
EXPORT_SIZE_BUCKETS = (
    64 * 1024,           # 64 KB
    1 * 1024 * 1024,     # 1 MB
    16 * 1024 * 1024,    # 16 MB
    128 * 1024 * 1024,   # 128 MB
    512 * 1024 * 1024,   # 512 MB
    2 * 1024 * 1024 * 1024,  # 2 GB
)
