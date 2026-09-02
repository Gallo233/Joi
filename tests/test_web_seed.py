from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agent_companion.core.character_packages import CharacterPackageManager
from agent_companion.web.prepare_seed import JOIDEBUG_WEB_CHARACTER_IDS, prepare_seed


class WebSeedTests(unittest.TestCase):
    def _write_package(self, packages: Path, character_id: str) -> None:
        package = packages / character_id
        package.mkdir(parents=True)
        motion = package / "assets" / "motions" / "official" / "VRMA_02.vrma"
        motion.parent.mkdir(parents=True)
        motion.write_bytes(f"motion:{character_id}".encode())
        (package / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": "joi.character.v1",
                    "id": character_id,
                    "version": "1.0.0",
                    "identity": {"name": character_id},
                    "appearance": {
                        "model_type": "static",
                        "motions": [{"motion": "greet", "animation": "assets/motions/official/VRMA_02.vrma"}],
                    },
                    "security": {"license": "test", "compatibility": ">=0.1.0", "built_in": False},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_joidebug_seed_copies_selected_packages_and_every_motion_byte(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            packages = root / "joidebug" / "data" / "agent_companion" / "characters" / "packages"
            for character_id in (*JOIDEBUG_WEB_CHARACTER_IDS, "builtin-hikari", "official-seed-san-vrm-test"):
                self._write_package(packages, character_id)
            avatar_tools = packages / "avatarsample-a" / "assets" / "voice" / ".transcribe-venv"
            avatar_tools.mkdir(parents=True)
            (avatar_tools / "activate_this.py").write_text("raise SystemExit\n", encoding="utf-8")
            transcriber = packages / "avatarsample-a" / "assets" / "voice" / "transcribe_local_whisper.py"
            transcriber.write_text("raise SystemExit\n", encoding="utf-8")
            source_runtime = root / "joidebug" / "data" / "agent_companion" / "characters" / "runtime"
            source_runtime.mkdir(parents=True)
            (source_runtime / "private-memory.sqlite3").write_bytes(b"private")
            guest_config = root / "guest.yaml"
            guest_config.write_text("llm: {}\n", encoding="utf-8")
            output = root / "seed"

            result = prepare_seed(
                output=output,
                guest_config=guest_config,
                character_source=root / "joidebug",
            )

            self.assertEqual(tuple(result["character_ids"]), JOIDEBUG_WEB_CHARACTER_IDS)
            output_characters = output / "data" / "agent_companion" / "characters"
            self.assertEqual(
                {path.name for path in (output_characters / "packages").iterdir()},
                set(JOIDEBUG_WEB_CHARACTER_IDS),
            )
            copied_motion = output_characters / "packages" / "avatarsample-a" / "assets" / "motions" / "official" / "VRMA_02.vrma"
            self.assertEqual(copied_motion.read_bytes(), b"motion:avatarsample-a")
            self.assertFalse((output_characters / "packages" / "avatarsample-a" / "assets" / "voice" / ".transcribe-venv").exists())
            self.assertFalse((output_characters / "packages" / "avatarsample-a" / "assets" / "voice" / "transcribe_local_whisper.py").exists())
            omitted = {(row["path"], row["reason"]) for row in result["omitted_development_files"]}
            self.assertIn(("assets/voice/.transcribe-venv/activate_this.py", "development environment"), omitted)
            self.assertIn(("assets/voice/transcribe_local_whisper.py", "executable helper"), omitted)
            self.assertEqual(
                json.loads((output / "web-seed-report.json").read_text(encoding="utf-8"))["omitted_development_files"],
                result["omitted_development_files"],
            )
            self.assertFalse(any((output_characters / "runtime").rglob("*.sqlite3")))

            # A real Core constructs a fresh manager for every copied session.
            # The curated marker must prevent that constructor from silently
            # adding the excluded built-in role back to the guest library.
            restarted = CharacterPackageManager(output)
            listed = restarted.list()
            self.assertEqual({row["id"] for row in listed["characters"]}, set(JOIDEBUG_WEB_CHARACTER_IDS))
            self.assertEqual(listed["active_id"], JOIDEBUG_WEB_CHARACTER_IDS[0])


if __name__ == "__main__":
    unittest.main()
