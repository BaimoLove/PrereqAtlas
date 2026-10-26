"""请求上下文中间件：为每个请求生成 request_id 并记录脱敏访问日志。"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infra.logging import sanitize_text

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_STATE_KEY = "request_id"

logger = logging.getLogger("app.http")


class RequestContextMiddleware:
    """纯 ASGI 中间件；不读取请求体，也不记录请求头。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        state: dict[str, Any] = scope.setdefault("state", {})
        state[REQUEST_ID_STATE_KEY] = request_id
        started = time.perf_counter()
        status_holder: dict[str, int | None] = {"status": None}

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            status = status_holder["status"]
            if status is not None:
                duration_ms = (time.perf_counter() - started) * 1000
                logger.info(
                    "request_id=%s method=%s path=%s status=%s duration_ms=%.1f",
                    request_id,
                    scope.get("method", ""),
                    sanitize_text(str(scope.get("path", ""))),
                    status,
                    duration_ms,
                )


class HeadMethodMiddleware:
    """按 HTTP 语义处理 HEAD：按 GET 路由匹配，但响应不返回正文。

    FastAPI 不会自动为 GET 路由注册 HEAD；这里不改 OpenAPI，也不新增业务路由。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "HEAD":
            await self.app(scope, receive, send)
            return

        scope = dict(scope)
        scope["method"] = "GET"

        async def send_without_body(message: Message) -> None:
            if message["type"] == "http.response.body":
                message = {**message, "body": b"", "more_body": False}
            await send(message)

        await self.app(scope, receive, send_without_body)
