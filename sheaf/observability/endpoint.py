"""Wires the /metrics endpoint per settings.

Three deployment shapes, controlled by `metrics_bind`:

  * `disabled` — no /metrics anywhere.

  * `main` — /metrics mounted on the FastAPI app, ALWAYS token-gated
    regardless of `metrics_auth` (sharing the public listener forces
    auth so an operator can't accidentally leak the surface).

  * `separate` — second listener bound to
    `metrics_bind_host:metrics_bind_port`. Auth optional per
    `metrics_auth`. The `none` case uses prometheus_client's
    `start_http_server` which is multiproc-aware out of the box.
    The `token` case mounts a Starlette app on the second port so
    we can validate a bearer header.
"""

from __future__ import annotations

import asyncio
import errno
import hmac
import logging
import socket
import threading

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest, start_http_server
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from sheaf.config import Settings
from sheaf.observability.registry import get_registry

logger = logging.getLogger("sheaf.metrics")

# Holds a reference to the separate-listener thread / server so we can
# log on shutdown. start_http_server returns (server, thread); we keep
# both so a future explicit stop is possible.
_separate_server: tuple | None = None


def _generate_response(registry) -> Response:
    data = generate_latest(registry)
    return PlainTextResponse(data, media_type=CONTENT_TYPE_LATEST)


def _verify_token(request: Request, expected: str) -> bool:
    """Constant-time bearer-token check."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    presented = auth[7:]
    return hmac.compare_digest(presented, expected)


def setup_metrics_endpoint(app: FastAPI, settings: Settings) -> None:
    """Wire /metrics per settings. Idempotent for repeated calls."""
    if not settings.metrics_enabled or settings.metrics_bind == "disabled":
        logger.info("metrics endpoint disabled")
        return

    registry = get_registry()

    if settings.metrics_bind == "main":
        # Always token-gated when sharing the public listener.
        token = settings.metrics_token

        @app.get("/metrics", include_in_schema=False)
        async def metrics(request: Request) -> Response:
            if not _verify_token(request, token):
                client = request.client.host if request.client else "?"
                logger.warning("metrics auth failed from %s", client)
                return PlainTextResponse("unauthorized", status_code=401)
            return _generate_response(registry)

        logger.info("metrics endpoint mounted on main app (token-gated)")
        return

    if settings.metrics_bind == "separate":
        # Multi-worker deployments (WEB_CONCURRENCY > 1): every worker
        # runs this lifespan and races to bind the metrics port. Losing
        # is fine - the multiprocess collector reads ALL workers' mmap
        # files, so any single worker serving the port exports complete
        # data. Without this tolerance the losing worker dies on
        # EADDRINUSE and the supervisor respawns it in a loop.
        if settings.metrics_auth == "token":
            _start_token_listener(
                settings.metrics_bind_host,
                settings.metrics_bind_port,
                settings.metrics_token,
                registry,
            )
        else:
            global _separate_server
            try:
                _separate_server = start_http_server(
                    port=settings.metrics_bind_port,
                    addr=settings.metrics_bind_host,
                    registry=registry,
                )
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                logger.info(
                    "metrics port %s:%d already served by another worker; skipping",
                    settings.metrics_bind_host,
                    settings.metrics_bind_port,
                )
                return
            logger.info(
                "metrics listener (no auth) on %s:%d",
                settings.metrics_bind_host,
                settings.metrics_bind_port,
            )


def _start_token_listener(host: str, port: int, token: str, registry) -> None:
    """Start a tiny Starlette app on the separate port for token-gated scraping.

    Runs in its own thread + event loop so it shares nothing with the
    FastAPI app's lifecycle. We rely on the process exiting to tear it
    down; no graceful shutdown path is needed for a metrics endpoint.
    """

    async def metrics(request: Request) -> Response:
        if not _verify_token(request, token):
            client = request.client.host if request.client else "?"
            logger.warning("metrics auth failed from %s", client)
            return PlainTextResponse("unauthorized", status_code=401)
        return _generate_response(registry)

    app = Starlette(routes=[Route("/metrics", metrics)])

    # Bind here, in the caller, so a lost bind race surfaces as a clean
    # skip instead of an async crash inside the listener thread (which
    # would kill the whole worker on multi-worker deployments - see the
    # comment at the call site).
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        if exc.errno != errno.EADDRINUSE:
            raise
        logger.info(
            "metrics port %s:%d already served by another worker; skipping",
            host, port,
        )
        return

    def _run() -> None:
        import uvicorn

        # log_level=warning suppresses the per-request access log noise
        # that would otherwise fill stdout with /metrics scrapes.
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        server = uvicorn.Server(config)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve(sockets=[sock]))

    t = threading.Thread(target=_run, name="metrics-listener", daemon=True)
    t.start()
    logger.info("metrics listener (token-gated) on %s:%d", host, port)
