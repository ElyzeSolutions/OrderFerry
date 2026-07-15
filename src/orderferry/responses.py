"""Canonical JSON protocol response builders."""

from __future__ import annotations

from typing import Any

Response = dict[str, Any]


def success(result: Any) -> Response:
    return {"ok": True, "result": result}


def error(
    method: str,
    code: str,
    message: str,
    *,
    retcode: int | None = None,
    last_error: tuple[Any, ...] | None = None,
) -> Response:
    detail: dict[str, Any] = {"code": code, "message": message, "method": method}
    if retcode is not None:
        detail["retcode"] = retcode
    if last_error is not None and len(last_error) >= 2:
        detail["last_error"] = {
            "retcode": last_error[0],
            "description": last_error[1],
        }
    return {"ok": False, "error": detail}
