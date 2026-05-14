from __future__ import annotations

import argparse
import asyncio
import json

import websockets


async def _run(url: str, text: str) -> None:
    async with websockets.connect(url) as ws:
        print(await ws.recv())
        await ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "smoke-1",
                    "method": "user.message",
                    "params": {"text": text},
                },
                ensure_ascii=False,
            )
        )
        print(await ws.recv())
        while True:
            try:
                print((await asyncio.wait_for(ws.recv(), timeout=3))[:320])
            except asyncio.TimeoutError:
                break


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8765")
    parser.add_argument("--text", default="你好")
    args = parser.parse_args()
    asyncio.run(_run(args.url, args.text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
