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
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, VoiceLine
from agent_companion.core.server import JsonRpcBridge


class CharacterPackageManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        (self.workspace / "agent_companion" / "config").mkdir(parents=True)
        (self.workspace / "agent_companion" / "config" / "default_character.yaml").write_text(
            "id: builtin-hikari\nname: Joi\npersona: 默认角色\nstyle:\n  tone: 温和\n",
            encoding="utf-8",
        )
        (self.workspace / "config.yaml").write_text(
            "characters:\n  - name: Joi\n    color: '#5b7ff5'\n    setting: 默认角色\n",
            encoding="utf-8",
        )
        self.manager = CharacterPackageManager(self.workspace)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_bootstraps_builtin_and_keeps_existing_memory_shared(self) -> None:
        result = self.manager.list()
        self.assertEqual(result["active_id"], "builtin-hikari")
        self.assertEqual(result["characters"][0]["name"], "Joi")
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

    def test_rejects_a_package_carrying_someone_elses_history_or_grants(self) -> None:
        """A character is portable; a user's history and decisions are not."""

        for field, value in (
            ("chat_history", [{"role": "user", "text": "上次我们聊到哪了"}]),
            ("memories", ["用户住在上海"]),
            ("permission_grants", {"computer_use": "granted"}),
            ("approved_skills", ["joi.computer_use"]),
        ):
            with self.subTest(field=field):
                with self.assertRaises(CharacterPackageError) as error:
                    self.manager.create({"identity": {"name": "带状态"}, field: value})
                self.assertEqual(error.exception.code, "package_user_state_blocked")
                self.assertEqual(error.exception.details.get("field"), field)

    def test_a_nested_permission_grant_is_found_too(self) -> None:
        with self.assertRaises(CharacterPackageError) as error:
            self.manager.create(
                {"identity": {"name": "嵌套"}, "capabilities": {"granted_permissions": ["files.delete"]}}
            )
        self.assertEqual(error.exception.code, "package_user_state_blocked")

    def test_empty_user_state_fields_are_allowed_and_stay_empty(self) -> None:
        created = self.manager.create(
            {
                "identity": {"name": "空字段"},
                "capabilities": {"requested_skills": ["joi.computer_use"], "approved_skills": []},
            }
        )
        detail = self.manager.detail(created["character"]["id"])["character"]["manifest"]
        self.assertEqual(detail["capabilities"]["approved_skills"], [])
        self.assertEqual(detail["capabilities"]["requested_skills"], ["joi.computer_use"])

    def test_provenance_records_the_archive_joi_read_not_the_one_it_claims(self) -> None:
        package_root = self.workspace / "claimed-source"
        package_root.mkdir()
        (package_root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": "joi.character.v1",
                    "id": "claimed",
                    "identity": {"name": "自称官方"},
                    "source": {"type": "official", "url": "https://characters.example/official"},
                    "provenance": {"format": "signed_official", "archive_sha256": "f" * 64},
                }
            ),
            encoding="utf-8",
        )
        archive_path = self.workspace / "claimed.joi-character"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(package_root / "manifest.json", "manifest.json")

        self.manager.import_package(archive_path)
        provenance = self.manager.detail("claimed")["character"]["manifest"]["provenance"]
        self.assertEqual(provenance["format"], "joi_character_archive")
        self.assertEqual(provenance["file_name"], "claimed.joi-character")
        self.assertNotEqual(provenance["archive_sha256"], "f" * 64)
        self.assertTrue(provenance["imported_at"] > 0)
        # What the author claimed is kept, but kept separate.
        source = self.manager.detail("claimed")["character"]["manifest"]["source"]
        self.assertEqual(source["type"], "official")

    def test_export_carries_the_character_but_not_this_machines_import_record(self) -> None:
        package_root = self.workspace / "roundtrip"
        package_root.mkdir()
        (package_root / "manifest.json").write_text(
            json.dumps({"schema": "joi.character.v1", "id": "roundtrip", "identity": {"name": "往返"}}),
            encoding="utf-8",
        )
        archive_path = self.workspace / "roundtrip.joi-character"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.write(package_root / "manifest.json", "manifest.json")
        self.manager.import_package(archive_path)
        self.assertTrue(self.manager.detail("roundtrip")["character"]["manifest"]["provenance"]["file_name"])

        exported = self.manager.export_package("roundtrip", self.workspace / "exports")
        with zipfile.ZipFile(exported["path"]) as archive:
            names = archive.namelist()
            shared = json.loads(archive.read("manifest.json"))
        self.assertEqual(shared["identity"]["name"], "往返")
        self.assertEqual(shared["provenance"]["file_name"], "")
        self.assertEqual(shared["provenance"]["archive_sha256"], "")
        self.assertEqual(shared["provenance"]["imported_at"], 0)
        for name in names:
            self.assertNotIn("memory", name.casefold())

    def test_authored_vrma_clips_are_resolved_for_the_stage(self) -> None:
        created = self.manager.create({"identity": {"name": "有动作"}, "security": {"license": "CC0"}})
        character_id = created["character"]["id"]
        self.manager.activate(character_id)
        root = self.manager.packages_dir / character_id
        motions = root / "assets" / "motions"
        motions.mkdir(parents=True, exist_ok=True)
        (motions / "idle.vrma").write_bytes(b"glTF-stub")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        manifest["appearance"]["motions"] = [
            {"motion": "idle", "animation": "assets/motions/idle.vrma"},
            {"motion": "greet", "animation": "assets/motions/missing.vrma"},
            {"motion": "talk"},
        ]
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        rows = {row["motion"]: row for row in self.manager.active_runtime_payload()["motion_mappings"]}
        self.assertTrue(rows["idle"]["animation_path"].endswith("assets/motions/idle.vrma"))
        # A clip the package names but does not ship leaves the motion on the
        # procedural fallback rather than pointing at nothing.
        self.assertNotIn("animation_path", rows["greet"])
        self.assertNotIn("animation_path", rows["talk"])

    def test_a_clip_outside_the_package_is_not_resolved(self) -> None:
        created = self.manager.create({"identity": {"name": "越界动作"}, "security": {"license": "CC0"}})
        character_id = created["character"]["id"]
        self.manager.activate(character_id)
        outside = self.workspace / "escape.vrma"
        outside.write_bytes(b"glTF-stub")
        root = self.manager.packages_dir / character_id
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        manifest["appearance"]["motions"] = [{"motion": "idle", "animation": "../../../escape.vrma"}]
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        rows = self.manager.active_runtime_payload()["motion_mappings"]
        self.assertNotIn("animation_path", rows[0])

    def test_only_vrma_is_accepted_as_an_animation(self) -> None:
        created = self.manager.create({"identity": {"name": "假动作"}, "security": {"license": "CC0"}})
        character_id = created["character"]["id"]
        self.manager.activate(character_id)
        root = self.manager.packages_dir / character_id
        motions = root / "assets" / "motions"
        motions.mkdir(parents=True, exist_ok=True)
        (motions / "idle.json").write_text("{}", encoding="utf-8")
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        manifest["appearance"]["motions"] = [{"motion": "idle", "animation": "assets/motions/idle.json"}]
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        rows = self.manager.active_runtime_payload()["motion_mappings"]
        self.assertNotIn("animation_path", rows[0])

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

    def test_switching_character_does_not_hand_over_the_conversation(self) -> None:
        """A new character starts its own thread, not the last one's.

        Switching used to relabel the current thread with the new character's
        id, leaving every previous turn in place for it to read. The shell
        cleared its own event list, so it looked separate while the model was
        still being handed the old transcript.
        """

        bridge = JsonRpcBridge(self.workspace)
        first = bridge.app.character_packages.create({"identity": {"name": "角色甲"}})["character"]["id"]
        second = bridge.app.character_packages.create({"identity": {"name": "角色乙"}})["character"]["id"]

        bridge.character_activate_command({"character_id": first})
        first_thread = bridge.collaboration.context()["thread_id"]
        bridge.collaboration.record_event(
            AgentEvent(
                type=EventType.TOOL_COMPLETED,
                task_id="task-1",
                agent_state={"tool": "companion.chat", "reply": "只属于角色甲的话"},
                display_card=DisplayCard("对话", "只属于角色甲的话"),
                voice_line=VoiceLine("只属于角色甲的话"),
            )
        )

        bridge.character_activate_command({"character_id": second})
        second_thread = bridge.collaboration.context()["thread_id"]
        self.assertNotEqual(second_thread, first_thread)
        carried = json.dumps(bridge.collaboration.history(second_thread), ensure_ascii=False)
        self.assertNotIn("只属于角色甲的话", carried)

        # Returning resumes that character's own thread rather than a new one.
        bridge.character_activate_command({"character_id": first})
        self.assertEqual(bridge.collaboration.context()["thread_id"], first_thread)

    def test_inheriting_the_conversation_is_possible_but_must_be_asked_for(self) -> None:
        bridge = JsonRpcBridge(self.workspace)
        first = bridge.app.character_packages.create({"identity": {"name": "承接甲"}})["character"]["id"]
        second = bridge.app.character_packages.create({"identity": {"name": "承接乙"}})["character"]["id"]

        bridge.character_activate_command({"character_id": first})
        thread_id = bridge.collaboration.context()["thread_id"]

        result = bridge.character_activate_command({"character_id": second, "inherit_conversation": True})
        self.assertTrue(result["thread"]["inherited"])
        self.assertEqual(bridge.collaboration.context()["thread_id"], thread_id)

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



class CharacterGreetingLanguageTests(unittest.TestCase):
    """A written greeting follows the chat language, not the spoken one."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.manager = CharacterPackageManager(Path(self.temporary.name) / "packages")
        self.manager.create(
            {
                "id": "bilingual",
                "identity": {"name": "Bilingual", "greeting": "我在。今天做点什么？"},
                "locale": "zh",
                "localizations": {"ja": {"identity": {"greeting": "います。何をしましょうか。"}}},
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_a_character_who_speaks_japanese_still_greets_a_chinese_chat_in_chinese(self) -> None:
        """The reported bug: a Japanese voice opened a Chinese conversation in Japanese."""

        self.manager.set_locale("bilingual", "ja")
        self.assertEqual(self.manager.active_locale("bilingual"), "ja")
        self.assertIn("我在", self.manager.greeting_for_chat_language("bilingual", "zh"))

    def test_the_greeting_follows_the_chat_language_when_the_package_has_it(self) -> None:
        self.assertIn("います", self.manager.greeting_for_chat_language("bilingual", "ja"))

    def test_an_unresolved_chat_language_uses_the_authored_language_not_the_spoken_one(self) -> None:
        # "Follow my input" has nothing to follow before the first message.
        self.manager.set_locale("bilingual", "ja")
        for value in ("follow", ""):
            self.assertIn("我在", self.manager.greeting_for_chat_language("bilingual", value), value)

    def test_a_chat_language_the_package_cannot_write_falls_back_rather_than_emptying(self) -> None:
        self.assertTrue(self.manager.greeting_for_chat_language("bilingual", "ko"))

    def test_an_unknown_character_yields_no_greeting_instead_of_raising(self) -> None:
        self.assertEqual(self.manager.greeting_for_chat_language("nobody", "zh"), "")


class CharacterModelFormatTests(unittest.TestCase):
    """The formats the stage grew into, checked where a package declares them."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _validate(self, model_type: str, filename: str = "", *, write: bool = True, case: str = "") -> dict:
        # One root per call: a file written by an earlier case would otherwise
        # satisfy a later "the file is missing" case.
        root = self.workspace / f"{model_type}-{case or filename or 'bare'}-{int(write)}"
        (root / "assets").mkdir(parents=True, exist_ok=True)
        if filename and write:
            (root / "assets" / filename).write_bytes(b"joi")
        manager = CharacterPackageManager(self.workspace / "packages")
        appearance: dict = {"model_type": model_type}
        if filename:
            appearance["model"] = f"assets/{filename}"
        return manager._appearance_report({"appearance": appearance}, root)

    def test_an_mmd_package_needs_a_pmx_or_pmd_and_is_told_physics_is_off(self) -> None:
        ok = self._validate("mmd", "model.pmx")
        self.assertTrue(ok["installable"], ok["errors"])
        self.assertEqual(ok["model_type"], "mmd")
        # Not an error: the model loads, it just stands stiffer than in MMD.
        self.assertTrue(any("物理" in str(item) for item in ok["warnings"]))
        wrong = self._validate("mmd", "model.fbx")
        self.assertFalse(wrong["installable"])

    def test_a_tachie_package_needs_an_image(self) -> None:
        ok = self._validate("tachie", "base.png")
        self.assertTrue(ok["installable"], ok["errors"])
        wrong = self._validate("tachie", "base.psd")
        self.assertFalse(wrong["installable"])
        missing = self._validate("tachie", "base.png", write=False)
        self.assertFalse(missing["installable"])

    def test_opaque_tachie_art_is_flagged_without_being_refused(self) -> None:
        """Sprite packs ship a contact sheet beside the real cut-outs.

        Picking the wrong one loads fine and then draws the backdrop as part of
        the character, which reads as a renderer fault rather than a chosen file.
        """

        import zlib

        def png(colour_type: int) -> bytes:
            header = b"\x89PNG\r\n\x1a\n"
            ihdr = struct.pack(">IIBBBBB", 8, 8, 8, colour_type, 0, 0, 0)
            chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
            return header + chunk

        root = self.workspace / "tachie-alpha"
        (root / "assets").mkdir(parents=True)
        (root / "assets" / "opaque.png").write_bytes(png(2))  # RGB
        (root / "assets" / "cutout.png").write_bytes(png(6))  # RGBA
        manager = CharacterPackageManager(self.workspace / "packages-alpha")

        opaque = manager._appearance_report({"appearance": {"model_type": "tachie", "model": "assets/opaque.png"}}, root)
        self.assertTrue(opaque["installable"], opaque["errors"])
        self.assertTrue(any("透明" in str(item) for item in opaque["warnings"]), opaque["warnings"])

        cutout = manager._appearance_report({"appearance": {"model_type": "tachie", "model": "assets/cutout.png"}}, root)
        self.assertFalse(any("透明" in str(item) for item in cutout["warnings"]), cutout["warnings"])

    def test_a_spine_package_is_refused_because_the_runtime_is_not_licensed(self) -> None:
        """Declarable so the reason can be shown; never installable."""

        result = self._validate("spine", "model.json")
        self.assertFalse(result["installable"])
        self.assertTrue(any("Spine" in str(item) for item in result["errors"]))

    def test_an_unknown_format_falls_back_to_static_rather_than_being_trusted(self) -> None:
        result = self._validate("hologram", "model.bin")
        self.assertEqual(result["model_type"], "static")



class CharacterImportUsabilityTests(unittest.TestCase):
    """Whether a package will work, reported beside whether it may be trusted."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.manager = CharacterPackageManager(self.workspace / "packages")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _members(self, names: list[str]) -> list[zipfile.ZipInfo]:
        return [zipfile.ZipInfo(name) for name in names]

    def test_macos_packaging_leftovers_are_reported_and_skipped(self) -> None:
        notes = self.manager._archive_usability_report(
            self._members(["model/joi.moc3", "__MACOSX/model/._joi.moc3", "model/._texture.png"])
        )
        self.assertTrue(any("__MACOSX" in note for note in notes), notes)

    def test_same_basename_in_two_folders_is_reported_as_a_silent_loser(self) -> None:
        """The loaders key assets by name, so one of the two never loads."""

        notes = self.manager._archive_usability_report(
            self._members(["a/texture.png", "b/texture.png", "c/other.png"])
        )
        self.assertTrue(any("同名文件" in note for note in notes), notes)

    def test_a_legacy_codepage_filename_is_reported_rather_than_silently_missing(self) -> None:
        # What CP437 decoding turns a Japanese expression filename into.
        notes = self.manager._archive_usability_report(self._members(["expressions/ÆËÐÑÒÓ.exp3.json"]))
        self.assertTrue(any("UTF-8" in note for note in notes), notes)

    def test_a_clean_archive_reports_nothing(self) -> None:
        self.assertEqual(self.manager._archive_usability_report(self._members(["model/joi.moc3", "model/t.png"])), [])

    def test_a_bare_moc3_gets_inferred_settings_instead_of_being_refused(self) -> None:
        root = self.workspace / "bare"
        (root / "assets").mkdir(parents=True)
        (root / "assets" / "girl.moc3").write_bytes(b"MOC3" + b"\x00" * 32)
        (root / "assets" / "texture_00.png").write_bytes(b"\x89PNG")
        report = self.manager._appearance_report({"appearance": {"model_type": "live2d"}}, root)
        self.assertTrue(report["installable"], report["errors"])
        self.assertTrue((root / "assets" / "girl.model3.json").is_file())
        settings = json.loads((root / "assets" / "girl.model3.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["FileReferences"]["Moc"], "girl.moc3")
        self.assertIn("texture_00.png", settings["FileReferences"]["Textures"])

    def test_a_file_named_moc3_that_is_not_one_is_still_refused(self) -> None:
        """Writing settings for it would turn a clear failure into a puzzling one."""

        root = self.workspace / "fake"
        (root / "assets").mkdir(parents=True)
        (root / "assets" / "girl.moc3").write_bytes(b"NOPE")
        report = self.manager._appearance_report({"appearance": {"model_type": "live2d"}}, root)
        self.assertFalse(report["installable"])
        self.assertFalse((root / "assets" / "girl.model3.json").exists())

    def test_two_moc3_files_are_not_guessed_between(self) -> None:
        root = self.workspace / "two"
        (root / "assets").mkdir(parents=True)
        for name in ("a.moc3", "b.moc3"):
            (root / "assets" / name).write_bytes(b"MOC3")
        report = self.manager._appearance_report({"appearance": {"model_type": "live2d"}}, root)
        self.assertFalse(report["installable"])



class CharacterAnimationClipTests(unittest.TestCase):
    """Authored clips: `.vrma` for a VRM rig, `.vmd` for an MMD one."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.manager = CharacterPackageManager(self.workspace / "packages")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _mappings(self, filename: str) -> list[dict]:
        root = self.workspace / f"pkg-{filename}"
        (root / "assets" / "motions").mkdir(parents=True)
        (root / "assets" / "motions" / filename).write_bytes(b"clip")
        manifest = {
            "appearance": {
                "motions": [{"motion": "greet", "animation": f"assets/motions/{filename}"}],
            }
        }
        return self.manager._motion_mappings_with_assets(manifest, root)

    def test_a_vmd_clip_is_published_the_same_way_a_vrma_is(self) -> None:
        for filename in ("wave.vmd", "wave.vrma"):
            rows = self._mappings(filename)
            self.assertEqual(len(rows), 1, filename)
            self.assertTrue(rows[0].get("animation_path"), filename)

    def test_a_clip_format_nothing_can_play_is_dropped(self) -> None:
        rows = self._mappings("wave.bvh")
        self.assertFalse(rows[0].get("animation_path"))


class CharacterModelStagingTests(unittest.TestCase):
    """A model that references siblings must arrive with them."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.manager = CharacterPackageManager(self.workspace / "packages")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create(self, model_type: str, model_name: str, siblings: list[str]) -> dict:
        source = self.workspace / "source"
        source.mkdir(parents=True, exist_ok=True)
        if model_name.endswith(".vrm"):
            # A real glTF 2.0 binary header, or install refuses it before staging.
            (source / model_name).write_bytes(b"glTF" + struct.pack("<II", 2, 12))
        else:
            (source / model_name).write_bytes(b"MOC3" if model_name.endswith(".moc3") else b"model")
        for name in siblings:
            (source / name).write_bytes(b"texture")
        # The same shape the Shell sends: identity and appearance are nested.
        return self.manager.create(
            {
                "identity": {"name": f"{model_type} tester"},
                "appearance": {"model_type": model_type, "model_path": str(source / model_name)},
            }
        )

    def test_an_mmd_model_is_installed_with_its_textures(self) -> None:
        result = self._create("mmd", "girl.pmx", ["body.png", "face.png"])
        self.assertTrue(result.get("ok"), result)
        root = self.manager.packages_dir / str(result["character"]["id"])
        staged = root / "assets" / "mmd"
        self.assertTrue((staged / "girl.pmx").is_file())
        # Without these the model loads and renders untextured, which reads as a
        # broken import rather than a missing file.
        self.assertTrue((staged / "body.png").is_file())
        self.assertTrue((staged / "face.png").is_file())

    def test_a_vrm_model_is_a_single_file_and_stays_one(self) -> None:
        result = self._create("vrm", "girl.vrm", ["unrelated.txt"])
        self.assertTrue(result.get("ok"), result)
        root = self.manager.packages_dir / str(result["character"]["id"])
        self.assertFalse((root / "assets" / "vrm" / "unrelated.txt").exists())


if __name__ == "__main__":
    unittest.main()
