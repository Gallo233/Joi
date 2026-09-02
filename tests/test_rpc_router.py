from __future__ import annotations

import asyncio
import json
import unittest

from agent_companion.core.rpc import (
    JsonRpcProtocolError,
    JsonRpcRouter,
    RpcMethodNotFound,
    encode_error,
    encode_result,
    parse_request,
)


class JsonRpcProtocolTests(unittest.TestCase):
    def test_parse_normalizes_object_params(self) -> None:
        request = parse_request('{"jsonrpc":"2.0","id":0,"method":"core.ping","params":null}')

        self.assertEqual(request.request_id, 0)
        self.assertEqual(request.method, "core.ping")
        self.assertEqual(request.params, {})

    def test_parse_rejects_invalid_json(self) -> None:
        with self.assertRaises(JsonRpcProtocolError) as context:
            parse_request("{")

        self.assertEqual(context.exception.code, -32700)

    def test_parse_rejects_missing_method_and_preserves_request_id(self) -> None:
        with self.assertRaises(JsonRpcProtocolError) as context:
            parse_request('{"jsonrpc":"2.0","id":"request-1"}')

        self.assertEqual(context.exception.code, -32600)
        self.assertEqual(context.exception.request_id, "request-1")

    def test_response_encoders_preserve_zero_id(self) -> None:
        result = json.loads(encode_result(0, {"ok": True}))
        error = json.loads(encode_error(0, -32601, "missing"))

        self.assertEqual(result["id"], 0)
        self.assertEqual(error["id"], 0)


class JsonRpcRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_sync_async_and_threaded_handlers(self) -> None:
        router = JsonRpcRouter()
        router.register("sync", lambda params: {"value": params["value"]})

        async def async_handler(params: dict[str, object]) -> dict[str, object]:
            return {"value": params["value"]}

        router.register("async", async_handler)
        router.register(
            "threaded",
            lambda params: {"value": params["value"]},
            run_in_thread=True,
            broadcast_ready=True,
        )

        sync_result = await router.dispatch("sync", {"value": 1})
        async_result = await router.dispatch("async", {"value": 2})
        threaded_result = await router.dispatch("threaded", {"value": 3})

        self.assertEqual(sync_result.result, {"value": 1})
        self.assertEqual(async_result.result, {"value": 2})
        self.assertEqual(threaded_result.result, {"value": 3})
        self.assertTrue(threaded_result.broadcast_ready)

    async def test_alias_uses_same_handler(self) -> None:
        router = JsonRpcRouter()
        router.register("voice.transcribe", lambda _: {"ok": True}, aliases=("audio.transcribe",))

        self.assertEqual((await router.dispatch("audio.transcribe", {})).result, {"ok": True})

    async def test_unknown_method_has_domain_specific_exception(self) -> None:
        router = JsonRpcRouter()

        with self.assertRaises(RpcMethodNotFound):
            await router.dispatch("missing", {})

    def test_duplicate_registration_is_rejected(self) -> None:
        router = JsonRpcRouter()
        router.register("core.ping", lambda _: {"ok": True})

        with self.assertRaises(ValueError):
            router.register("core.ping", lambda _: {"ok": False})


if __name__ == "__main__":
    unittest.main()
