import hmac
import json

from starlette.types import ASGIApp, Receive, Scope, Send

TOKEN_HEADER = b"x-pet-token"
SUBPROTOCOL_HEADER = b"sec-websocket-protocol"
WS_SUBPROTOCOL = "pet.v1"


def _subprotocol_offers_token(headers: dict[bytes, bytes], token: bytes) -> bool:
    raw = headers.get(SUBPROTOCOL_HEADER, b"").decode("latin-1")
    parts = (part.strip().encode("utf-8") for part in raw.split(","))
    return any(hmac.compare_digest(part, token) for part in parts if part)


class TokenAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        provided = headers.get(TOKEN_HEADER, b"")
        if hmac.compare_digest(provided, self._token):
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            if _subprotocol_offers_token(headers, self._token):
                await self.app(scope, receive, send)
                return
            await send({"type": "websocket.close", "code": 1008})
            return

        body = json.dumps(
            {"error": {"code": "unauthorized", "message": "缺少或无效的 X-Pet-Token"}},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
