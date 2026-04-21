"""Global request-body size cap.

Rejects oversized request bodies before the application has a chance to
buffer them. Without this, a 10 GB POST is happily spooled to disk by
Starlette's multipart parser before the per-endpoint size check ever
runs — a trivial DoS vector on any public instance.

Two-stage enforcement:
1. Fast path: if the client sent a Content-Length, reject at 413 before
   reading a single byte of body.
2. Streaming path: for chunked transfer encoding (no Content-Length),
   tally body bytes as they arrive from the ASGI transport and raise
   BodyTooLargeError once the cap is exceeded. A FastAPI exception
   handler converts that to a 413 response.
"""

from __future__ import annotations

import json


class BodyTooLargeError(Exception):
    """Raised from the wrapped receive when the streaming body cap is hit."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__("request body exceeds cap")
        self.max_bytes = max_bytes


class MaxBodySizeMiddleware:
    """Pure-ASGI middleware enforcing a global max request-body size."""

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    break
                if length > self.max_bytes:
                    await _send_413(send, self.max_bytes)
                    return
                break

        received = 0
        max_bytes = self.max_bytes

        async def wrapped_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise BodyTooLargeError(max_bytes)
            return message

        await self.app(scope, wrapped_receive, send)


async def _send_413(send, max_bytes: int) -> None:
    body = json.dumps(
        {"detail": f"Request body too large. Max: {max_bytes // (1024 * 1024)}MB"}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
