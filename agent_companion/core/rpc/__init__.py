"""Transport-neutral JSON-RPC primitives used by Joi's local interfaces."""

from agent_companion.core.rpc.protocol import (
    JsonRpcProtocolError,
    JsonRpcRequest,
    encode_error,
    encode_result,
    parse_request,
)
from agent_companion.core.rpc.router import JsonRpcRouter, RpcDispatchResult, RpcMethodNotFound

__all__ = [
    "JsonRpcProtocolError",
    "JsonRpcRequest",
    "JsonRpcRouter",
    "RpcDispatchResult",
    "RpcMethodNotFound",
    "encode_error",
    "encode_result",
    "parse_request",
]
