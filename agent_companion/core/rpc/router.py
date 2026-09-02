from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Union


RpcResult = dict[str, Any]
RpcHandlerResult = Union[RpcResult, Awaitable[RpcResult]]
RpcHandler = Callable[[dict[str, Any]], RpcHandlerResult]


class RpcMethodNotFound(LookupError):
    def __init__(self, method: str) -> None:
        super().__init__(method)
        self.method = method


@dataclass(frozen=True)
class RpcMethod:
    handler: RpcHandler
    run_in_thread: bool = False
    broadcast_ready: bool = False


@dataclass(frozen=True)
class RpcDispatchResult:
    result: RpcResult
    broadcast_ready: bool = False


class JsonRpcRouter:
    """Declarative method registry independent from WebSocket transport code."""

    def __init__(self) -> None:
        self._methods: dict[str, RpcMethod] = {}

    def register(
        self,
        name: str,
        handler: RpcHandler,
        *,
        aliases: tuple[str, ...] = (),
        run_in_thread: bool = False,
        broadcast_ready: bool = False,
    ) -> None:
        spec = RpcMethod(
            handler=handler,
            run_in_thread=run_in_thread,
            broadcast_ready=broadcast_ready,
        )
        for method_name in (name, *aliases):
            normalized = method_name.strip()
            if not normalized:
                raise ValueError("RPC method name must not be empty")
            if normalized in self._methods:
                raise ValueError(f"RPC method already registered: {normalized}")
            self._methods[normalized] = spec

    async def dispatch(self, method: str, params: dict[str, Any]) -> RpcDispatchResult:
        spec = self._methods.get(method)
        if spec is None:
            raise RpcMethodNotFound(method)
        if spec.run_in_thread:
            result = await asyncio.to_thread(spec.handler, params)
        else:
            result = spec.handler(params)
            if inspect.isawaitable(result):
                result = await result
        if not isinstance(result, dict):
            raise TypeError(f"RPC handler {method!r} returned a non-object result")
        return RpcDispatchResult(result=result, broadcast_ready=spec.broadcast_ready)

    def methods(self) -> tuple[str, ...]:
        return tuple(sorted(self._methods))
