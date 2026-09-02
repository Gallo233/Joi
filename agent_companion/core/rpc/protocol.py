from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class JsonRpcRequest:
    """A normalized JSON-RPC request accepted by Joi's local transports."""

    request_id: Any
    method: str
    params: dict[str, Any]


class JsonRpcProtocolError(ValueError):
    def __init__(self, code: int, message: str, request_id: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


def parse_request(raw: str) -> JsonRpcRequest:
    """Decode and validate the small JSON-RPC subset used by Joi.

    Joi commands use named object parameters. For backwards compatibility a
    missing or non-object ``params`` value is normalized to an empty mapping.
    """

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise JsonRpcProtocolError(-32700, "parse error") from exc
    if not isinstance(payload, dict):
        raise JsonRpcProtocolError(-32600, "invalid request")
    request_id = payload.get("id")
    method = payload.get("method")
    if not isinstance(method, str) or not method.strip():
        raise JsonRpcProtocolError(-32600, "invalid request", request_id)
    params = payload.get("params")
    return JsonRpcRequest(
        request_id=request_id,
        method=method.strip(),
        params=params if isinstance(params, dict) else {},
    )


def encode_result(request_id: Any, result: dict[str, Any]) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        ensure_ascii=False,
    )


def encode_error(request_id: Any, code: int, message: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        },
        ensure_ascii=False,
    )
