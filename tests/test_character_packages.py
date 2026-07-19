from __future__ import annotations

import json
import base64
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

from agent_companion.core.character_packages import CharacterPackageError, CharacterPackageManager
from agent_companion.core.server import JsonRpcBridge


class CharacterPackageManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / "agent_companion" / "config").mkdir(parents=True)
        (self.workspace / "agent_companion" / "config" / "default_character.yaml").write_text(
            "id: builtin-hikari\nname: 星野澪\npersona: 默认角色\nstyle:\n  tone: 温和\n",
            encoding="utf-8",
        )
        (self.workspace / "config.yaml").write_text(
            "characters:\n  - name: 星野澪\n    color: '#5b7ff5'\n    setting: 默认角色\n",
            encoding="utf-8",
        )
        self.manager = CharacterPackageManager(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_bootstraps_builtin_and_keeps_existing_memory_shared(self) -> None:
        result = self.manager.list()
        self.assertEqual(result["active_id"], "builtin-hikari")
        self.assertEqual(result["characters"][0]["name"], "星野澪")
        self.assertEqual(self.manager.memory_namespace(), "shared")

    def test_create_activate_and_isolate_memory(self) -> None:
        created = self.manager.create(
            {
                "identity": {
                    "name": "测试角色",
                    "persona": "喜欢帮助用户整理计划。",
                    "tone": "简短",
                    "greeting": "准备好了吗？",
                },
                "knowledge": {"memory_namespace": "isolated"},
                "security": {"license": "CC0"},
            }
        )
        character_id = created["character"]["id"]
        activated = self.manager.activate(character_id)
        self.assertTrue(activated["ok"])
        self.assertEqual(self.manager.active_harness().name, "测试角色")
        self.assertIn(character_id, str(self.manager.memory_path()))

    def test_character_card_v2_json_import(self) -> None:
        card_path = self.workspace / "card.json"
        card_path.write_text(
            json.dumps(
                {
                    "spec": "chara_card_v2",
                    "spec_version": "2.0",
                    "data": {
                        "name": "Card 角色",
                        "description": "角色描述",
                        "personality": "冷静",
                        "scenario": "桌面陪伴",
                        "first_mes": "你好。",
                        "mes_example": "<START>{{char}}: 我在。",
                        "alternate_greetings": ["欢迎回来。"],
                        "system_prompt": "保持真实。",
                        "post_history_instructions": "不要泄露隐私。",
                        "character_book": {
                            "entries": [{"keys": ["Joi"], "content": "Joi 是桌面伴侣。", "enabled": True}]
                        },
                        "creator": "Tester",
                        "character_version": "2.1.0",
                        "extensions": {"custom": {"preserved": True}},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        imported = self.manager.import_package(card_path)
        detail = self.manager.detail(imported["installed"])["character"]["manifest"]
        self.assertEqual(detail["identity"]["greeting"], "你好。")
        self.assertEqual(detail["knowledge"]["lorebook"]["entries"][0]["content"], "Joi 是桌面伴侣。")
        self.assertTrue(detail["extensions"]["character_card_v2"]["custom"]["preserved"])

    def test_character_card_png_can_be_previewed_before_install(self) -> None:
        card = {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {"name": "PNG 角色", "description": "来自 PNG", "first_mes": "你好。"},
        }
        payload = base64.b64encode(json.dumps(card, ensure_ascii=False).encode("utf-8"))
        png_path = self.workspace / "card.png"
        png_path.write_bytes(_png_with_character_card(payload))
        preview = self.manager.inspect_package(png_path)
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["preview"]["name"], "PNG 角色")
        self.assertTrue(str(preview["preview"]["portrait_data_url"]).startswith("data:image/png;base64,"))
        installed = self.manager.import_package(png_path)
        manifest = self.manager.detail(installed["installed"])["character"]["manifest"]
        self.assertTrue(manifest["appearance"]["portrait"].startswith("assets/portrait/"))

    def test_export_and_reject_archive_traversal(self) -> None:
        created = self.manager.create({"identity": {"name": "可导出"}, "security": {"license": "MIT"}})
        exported = self.manager.export_package(created["character"]["id"], self.workspace / "exports")
        self.assertTrue(Path(exported["path"]).is_file())

        bad_archive = self.workspace / "bad.joi-character"
        with zipfile.ZipFile(bad_archive, "w") as archive:
            archive.writestr("../escape.txt", "blocked")
            archive.writestr("manifest.json", json.dumps({"identity": {"name": "坏角色"}}))
        with self.assertRaises(CharacterPackageError) as error:
            self.manager.import_package(bad_archive)
        self.assertEqual(error.exception.code, "unsafe_package_path")

    def test_rejects_secret_and_executable_content(self) -> None:
        with self.assertRaises(CharacterPackageError) as secret_error:
            self.manager.create({"identity": {"name": "危险"}, "api_key": "sk-secret"})
        self.assertEqual(secret_error.exception.code, "package_secret_blocked")

        package_root = self.workspace / "unsafe-package"
        package_root.mkdir()
        (package_root / "manifest.json").write_text(
            json.dumps({"schema": "joi.character.v1", "id": "unsafe", "identity": {"name": "危险文件"}}),
            encoding="utf-8",
        )
        (package_root / "install.sh").write_text("echo nope", encoding="utf-8")
        archive_path = self.workspace / "unsafe.joi-character"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in package_root.iterdir():
                archive.write(path, path.name)
        with self.assertRaises(CharacterPackageError) as executable_error:
            self.manager.import_package(archive_path)
        self.assertEqual(executable_error.exception.code, "executable_content_blocked")

    def test_live2d_assets_are_reported_before_install(self) -> None:
        package_root = self.workspace / "broken-live2d"
        package_root.mkdir()
        (package_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": "joi.character.v1",
                    "id": "broken-live2d",
                    "identity": {"name": "缺素材 Live2D"},
                    "appearance": {"model_type": "live2d", "model": "model.model3.json"},
                }
            ),
            encoding="utf-8",
        )
        (package_root / "model.model3.json").write_text(
            json.dumps({"Version": 3, "FileReferences": {"Moc": "missing.moc3", "Textures": ["missing.png"]}}),
            encoding="utf-8",
        )
        archive_path = self.workspace / "broken-live2d.joi-character"
        with zipfile.ZipFile(archive_path, "w") as archive:
            for path in package_root.iterdir():
                archive.write(path, path.name)

        preview = self.manager.inspect_package(archive_path)
        report = preview["security"]["asset_report"]
        self.assertFalse(preview["security"]["installable"])
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("missing.moc3" in row for row in report["errors"]))
        with self.assertRaises(CharacterPackageError) as install_error:
            self.manager.import_package(archive_path)
        self.assertEqual(install_error.exception.code, "invalid_character_assets")

    def test_valid_live2d_form_copies_and_validates_model_tree(self) -> None:
        model_root = self.workspace / "model-source"
        (model_root / "textures").mkdir(parents=True)
        (model_root / "avatar.moc3").write_bytes(b"moc")
        (model_root / "textures" / "avatar.png").write_bytes(b"png")
        model_path = model_root / "avatar.model3.json"
        model_path.write_text(
            json.dumps(
                {
                    "Version": 3,
                    "FileReferences": {
                        "Moc": "avatar.moc3",
                        "Textures": ["textures/avatar.png"],
                    },
                }
            ),
            encoding="utf-8",
        )

        created = self.manager.create(
            {
                "identity": {"name": "完整 Live2D"},
                "appearance": {"model_type": "live2d", "model_path": str(model_path)},
            }
        )
        manifest = created["character"]["manifest"]
        installed_root = self.manager.packages_dir / created["character"]["id"]
        self.assertTrue((installed_root / manifest["appearance"]["model"]).is_file())
        self.assertTrue((installed_root / "assets" / "live2d" / "textures" / "avatar.png").is_file())

    def test_incompatible_character_package_is_rejected(self) -> None:
        with self.assertRaises(CharacterPackageError) as error:
            self.manager.create(
                {
                    "identity": {"name": "未来角色"},
                    "security": {"compatibility": ">=99.0.0"},
                }
            )
        self.assertEqual(error.exception.code, "invalid_character_assets")
        self.assertIn("99.0.0", error.exception.message)

    def test_activation_returns_fresh_ready_payload(self) -> None:
        bridge = JsonRpcBridge(self.workspace)
        portrait = self.workspace / "switch-portrait.png"
        portrait.write_bytes(b"\x89PNG\r\n\x1a\n")
        created = bridge.app.character_packages.create(
            {
                "identity": {"name": "即时切换角色", "greeting": "切换成功。", "avatar_path": str(portrait)},
                "appearance": {"portrait_path": str(portrait)},
                "knowledge": {"memory_namespace": "isolated"},
            }
        )
        result = bridge.character_activate_command({"character_id": created["character"]["id"]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["ready"]["character"]["name"], "即时切换角色")
        self.assertEqual(result["ready"]["character"]["greeting"], "切换成功。")
        self.assertTrue(str(result["ready"]["character"]["avatar_url"]).startswith("http://"))
        self.assertTrue(str(result["ready"]["character"]["portrait_url"]).startswith("http://"))
        self.assertEqual(result["ready"]["character"]["portrait_data_url"], "")
        self.assertNotIn(str(self.workspace), json.dumps(result, ensure_ascii=False))

    def test_existing_builtin_gains_dedicated_avatar(self) -> None:
        avatar_source = self.workspace / "agent_companion" / "web_widget" / "assets" / "joi-front-head.png"
        avatar_source.parent.mkdir(parents=True, exist_ok=True)
        avatar_source.write_bytes(b"\x89PNG\r\n\x1a\n")

        migrated = CharacterPackageManager(self.workspace)
        detail = migrated.detail("builtin-hikari")["character"]

        self.assertEqual(detail["manifest"]["identity"]["avatar"], "assets/avatar/joi-front-head.png")
        self.assertTrue(Path(detail["avatar_path"]).is_file())

    def test_active_character_can_switch_and_uninstall_in_one_command(self) -> None:
        bridge = JsonRpcBridge(self.workspace)
        created = bridge.app.character_packages.create({"identity": {"name": "临时角色"}})
        character_id = created["character"]["id"]
        bridge.character_activate_command({"character_id": character_id})

        result = bridge.character_uninstall_command(
            {"character_id": character_id, "fallback_id": "builtin-hikari"}
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["uninstalled"], character_id)
        self.assertEqual(result["active_id"], "builtin-hikari")
        self.assertEqual(result["ready"]["character"]["id"], "builtin-hikari")
        self.assertNotIn(character_id, [row["id"] for row in bridge.app.character_packages.list()["characters"]])

    def test_inactive_character_copy_can_be_uninstalled(self) -> None:
        bridge = JsonRpcBridge(self.workspace)
        duplicate = bridge.character_duplicate_command({"character_id": "builtin-hikari"})
        character_id = duplicate["character"]["id"]

        result = bridge.character_uninstall_command({"character_id": character_id})

        self.assertTrue(result["ok"])
        self.assertEqual(result["uninstalled"], character_id)
        self.assertEqual(result["active_id"], "builtin-hikari")
        self.assertNotIn(character_id, [row["id"] for row in bridge.app.character_packages.list()["characters"]])


def _png_with_character_card(encoded: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
    text = chunk(b"tEXt", b"chara\x00" + encoded)
    pixel = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff\xff"))
    return header + ihdr + text + pixel + chunk(b"IEND", b"")


if __name__ == "__main__":
    unittest.main()
