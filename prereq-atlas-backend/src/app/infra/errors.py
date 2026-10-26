"""统一异常类型与 HTTP 错误转换。

所有错误响应使用 ``{code,message,details,request_id}``；不返回堆栈、连接串
或请求原文。框架错误（404/405/422/503）也转换为同一结构。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.contracts import ERROR_STATUS, ErrorCode, ErrorResponse
from app.infra.logging import sanitize_text

logger = logging.getLogger("app.errors")

_FIXED_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.AUTHENTICATION_REQUIRED: "需要有效登录",
    ErrorCode.FORBIDDEN: "权限不足",
    ErrorCode.NOT_FOUND: "请求的资源不存在",
    ErrorCode.METHOD_NOT_ALLOWED: "请求方法不允许",
    ErrorCode.CONFLICT: "请求冲突",
    ErrorCode.PAYLOAD_TOO_LARGE: "请求体过大",
    ErrorCode.VALIDATION_ERROR: "输入不合法",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",
    ErrorCode.SERVICE_UNAVAILABLE: "服务暂不可用",
}

_STATUS_TO_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.AUTHENTICATION_REQUIRED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    422: ErrorCode.VALIDATION_ERROR,
    500: ErrorCode.INTERNAL_ERROR,
    503: ErrorCode.SERVICE_UNAVAILABLE,
}


class AppError(Exception):
    """业务异常；HTTP 状态由错误码映射，details 保持结构化。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.message = message or _FIXED_MESSAGES[code]
        self.details = dict(details) if details is not None else None
        super().__init__(self.message)

    @property
    def status_code(self) -> int:
        return ERROR_STATUS[self.code]


def request_id_of(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        return request_id
    return str(uuid.uuid4())


def _error_response(
    request: Request,
    code: ErrorCode,
    message: str,
    *,
    details: Mapping[str, object] | None = None,
    status_code: int | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = request_id_of(request)
    body = ErrorResponse(
        code=code,
        message=message,
        details=dict(details) if details is not None else None,
        request_id=request_id,
    )
    response_headers = {"X-Request-ID": request_id}
    if headers:
        response_headers.update(dict(headers))
    return JSONResponse(
        status_code=status_code if status_code is not None else ERROR_STATUS[code],
        content=body.model_dump(mode="json"),
        headers=response_headers,
    )


def _log_error(request: Request, level: int, message: str) -> None:
    logger.log(level, "request_id=%s %s", request_id_of(request), sanitize_text(message))


def _validation_details(exc: RequestValidationError) -> dict[str, object]:
    errors: list[dict[str, str]] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ())]
        if location and location[0] in {"body", "query", "path", "header", "cookie"}:
            location = location[1:]
        errors.append(
            {
                "field": ".".join(location) or "body",
                "reason": str(error.get("msg", "输入不合法")),
            }
        )
    return {"errors": errors}


def install_error_handlers(app: FastAPI) -> None:
    """注册统一异常处理器；应在应用创建时调用一次。"""

    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        _log_error(request, logging.WARNING, f"code={exc.code.value} message={exc.message}")
        return _error_response(request, exc.code, exc.message, details=exc.details)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        _log_error(request, logging.WARNING, "code=VALIDATION_ERROR")
        return _error_response(
            request,
            ErrorCode.VALIDATION_ERROR,
            _FIXED_MESSAGES[ErrorCode.VALIDATION_ERROR],
            details=_validation_details(exc),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        _log_error(
            request,
            logging.WARNING,
            f"code={code.value} status={exc.status_code}",
        )
        return _error_response(
            request,
            code,
            _FIXED_MESSAGES[code],
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # 只记录异常类型与脱敏后的文本；不返回堆栈，也不把原文放进响应。
        _log_error(
            request,
            logging.ERROR,
            f"code=INTERNAL_ERROR error={type(exc).__name__}: {exc}",
        )
        return _error_response(
            request,
            ErrorCode.INTERNAL_ERROR,
            _FIXED_MESSAGES[ErrorCode.INTERNAL_ERROR],
            status_code=500,
        )


__all__ = ["AppError", "install_error_handlers", "request_id_of"]
