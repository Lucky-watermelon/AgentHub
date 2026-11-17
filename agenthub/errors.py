"""统一错误码与异常：所有错误响应都带 code / message / retryable。"""
from __future__ import annotations


class ApiError(Exception):
    status_code = 400
    code = "AH-COMMON-400"
    retryable = False

    def __init__(self, message: str, code: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class NotFound(ApiError):
    status_code = 404
    code = "AH-COMMON-404"


class DagInvalid(ApiError):
    status_code = 422
    code = "AH-RUNTIME-422"

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class ToolFailed(ApiError):
    code = "AH-TOOL-500"


class ToolTimeout(ApiError):
    status_code = 504
    code = "AH-TOOL-504"
    retryable = True


class ModelUnavailable(ApiError):
    status_code = 502
    code = "AH-MODEL-502"
    retryable = True


class AuthRequired(ApiError):
    status_code = 401
    code = "AH-AUTH-401"
