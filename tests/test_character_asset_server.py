from __future__ import annotations

import functools
import http.server
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_companion.core.server import JsonRpcBridge, _CharacterAssetRequestHandler


class CharacterAssetServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.packages_dir = Path(self.temp.name) / "characters" / "packages"
        model_dir = self.packages_dir / "momose-hiyori" / "assets" / "live2d"
        model_dir.mkdir(parents=True)
        (model_dir / "hiyori.model3.json").write_text(
            '{"FileReferences":{"Textures":["texture_00.png"]}}',
            encoding="utf-8",
        )
        (model_dir / "texture_00.png").write_bytes(b"hiyori-texture")

        self.session_token = "test-session-token"
        handler = functools.partial(
            _CharacterAssetRequestHandler,
            directory=str(self.packages_dir),
            health_provider=lambda ready: {"ok": True, "ready": ready},
            session_token=self.session_token,
        )
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def _fetch(self, url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=2) as response:
            self.assertEqual(response.status, 200)
            return response.read()

    def _assert_not_found(self, url: str) -> None:
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(url, timeout=2)
        self.assertEqual(error.exception.code, 404)

    def test_character_asset_url_places_token_in_path_for_relative_resources(self) -> None:
        bridge = JsonRpcBridge.__new__(JsonRpcBridge)
        bridge.host = "127.0.0.1"
        bridge.asset_port = int(self.server.server_address[1])
        bridge.session_token = self.session_token
        bridge.app = SimpleNamespace(
            character_packages=SimpleNamespace(packages_dir=self.packages_dir),
        )

        model_path = self.packages_dir / "momose-hiyori" / "assets" / "live2d" / "hiyori.model3.json"
        model_url = bridge._character_asset_url(model_path)

        self.assertIn(f"/characters/{self.session_token}/", model_url)
        self.assertEqual(urllib.parse.urlsplit(model_url).query, "")
        self.assertIn(b"FileReferences", self._fetch(model_url))
        self.assertEqual(
            self._fetch(urllib.parse.urljoin(model_url, "texture_00.png")),
            b"hiyori-texture",
        )

    def test_character_asset_server_rejects_missing_or_wrong_token(self) -> None:
        relative = "momose-hiyori/assets/live2d/hiyori.model3.json"

        self._assert_not_found(f"{self.origin}/characters/{relative}")
        self._assert_not_found(f"{self.origin}/characters/wrong-token/{relative}")
        self.assertIn(
            b"FileReferences",
            self._fetch(f"{self.origin}/characters/{self.session_token}/{relative}"),
        )

    def test_character_asset_server_accepts_legacy_query_token(self) -> None:
        relative = "momose-hiyori/assets/live2d/hiyori.model3.json"

        self.assertIn(
            b"FileReferences",
            self._fetch(
                f"{self.origin}/characters/{relative}?token="
                f"{urllib.parse.quote(self.session_token, safe='')}"
            ),
        )

    def test_guest_character_list_uses_model_url_without_local_path(self) -> None:
        bridge = JsonRpcBridge.__new__(JsonRpcBridge)
        bridge.host = "127.0.0.1"
        bridge.asset_port = int(self.server.server_address[1])
        bridge.session_token = self.session_token
        bridge.guest_mode = True
        model_path = self.packages_dir / "momose-hiyori" / "assets" / "live2d" / "hiyori.model3.json"
        character_packages = SimpleNamespace(
            packages_dir=self.packages_dir,
            list=lambda: {
                "ok": True,
                "active_id": "momose-hiyori",
                "characters": [{"id": "momose-hiyori", "model_path": str(model_path)}],
            },
        )
        bridge.app = SimpleNamespace(character_packages=character_packages)

        result = bridge.character_list_command()

        character = result["characters"][0]
        self.assertNotIn("model_path", character)
        self.assertIn(f"/characters/{self.session_token}/", character["model_url"])

    def test_public_sprite_prefers_asset_url_over_inline_image(self) -> None:
        bridge = JsonRpcBridge.__new__(JsonRpcBridge)
        bridge.host = "127.0.0.1"
        bridge.asset_port = int(self.server.server_address[1])
        bridge.session_token = self.session_token
        bridge.app = SimpleNamespace(
            character_packages=SimpleNamespace(packages_dir=self.packages_dir),
        )
        image_path = self.packages_dir / "momose-hiyori" / "assets" / "live2d" / "texture_00.png"

        sprite = bridge._public_character_sprite({"id": "neutral", "image_path": str(image_path)})

        self.assertIn(f"/characters/{self.session_token}/", sprite["image_url"])
        self.assertEqual(sprite["image_data_url"], "")

    def test_character_asset_server_rejects_traversal(self) -> None:
        self._assert_not_found(
            f"{self.origin}/characters/{self.session_token}/%2E%2E/secret.txt"
        )


if __name__ == "__main__":
    unittest.main()
