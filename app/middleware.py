"""Request-body size guard.

Starlette parses (and spools) a multipart upload before the endpoint runs, so a limit
checked only in the endpoint would let an arbitrarily large body reach disk first. This
ASGI middleware counts the bytes actually received and stops the request as soon as the
budget is exceeded. A declared Content-Length is used only to reject early, never trusted
as the size.
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import ApiError, error_body


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return

        limit = self.max_body_bytes
        too_large = ApiError(
            413, "UPLOAD_TOO_LARGE", f"Request body exceeds the {limit}-byte upload limit."
        )

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = 0
                if declared > limit:
                    await _send_error(send, too_large)
                    return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise too_large
            return message

        try:
            await self.app(scope, limited_receive, send)
        except ApiError as exc:  # raised outside FastAPI's exception handling
            if exc is not too_large:
                raise
            await _send_error(send, exc)


async def _send_error(send: Send, exc: ApiError) -> None:
    body = json.dumps(error_body(exc.code, exc.message)).encode()
    await send(
        {
            "type": "http.response.start",
            "status": exc.status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
