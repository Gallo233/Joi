from __future__ import annotations

import tempfile
import asyncio
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

from agent_companion.core.byok import ByokService
from agent_companion.core.config import ModelRouter, load_app_config
from agent_companion.core.schemas import ToolRequest
from agent_companion.core.server import JsonRpcBridge
from agent_companion.core.tools.chat import CompanionChatTool


class ByokServiceTests(unittest.TestCase):
    def test_rpc_surface_exposes_complete_byok_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            character_dir = workspace / "agent_companion" / "config"
            character_dir.mkdir(parents=True)
            source = Path(__file__).resolve().parents[1] / "agent_companion" / "config" / "default_character.yaml"
            shutil.copy2(source, character_dir / "default_character.yaml")
            bridge = JsonRpcBridge(workspace)

            self.assertTrue({"byok.status", "byok.connect", "byok.test", "byok.models", "byok.disconnect"}.issubset(bridge.rpc.methods()))
            with patch("agent_companion.core.byok.managed_secret_status", return_value={"stored": False, "source": "missing", "secure_store_available": True}):
                result = asyncio.run(bridge.rpc.dispatch("byok.status", {}))
            self.assertEqual(result.result["state"], "not_configured")
            with patch("agent_companion.core.server.subprocess.run") as external_probe:
                ready = bridge._ready_payload()
            external_probe.assert_not_called()
            self.assertIn(ready["joi_mcp"]["status"], {"not_checked", "codex_not_found"})

    def test_connect_creates_config_without_plaintext_secret_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            reloads: list[bool] = []
            service = ByokService(workspace, lambda: reloads.append(True))
            with (
                patch("agent_companion.core.byok.store_managed_secret", return_value=(True, "")) as store,
                patch("agent_companion.core.byok.managed_secret", return_value="sk-stored"),
                patch("agent_companion.core.byok.managed_secret_status", return_value={"stored": True, "source": "system", "secure_store_available": True}),
                patch.object(service, "test", return_value={"ok": True, "error": "", "models": ["gpt-5.6-luna"]}),
            ):
                result = service.connect(
                    {
                        "provider": "openai",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-5.6-luna",
                        "api_key": "sk-test-value",
                        "temperature": 0.7,
                    }
                )

            self.assertTrue(result["ok"])
            self.assertTrue(result["saved"])
            self.assertEqual(reloads, [True])
            store.assert_called_once_with("JOI_LLM_API_KEY", "sk-test-value")
            raw_text = (workspace / "config.yaml").read_text(encoding="utf-8")
            self.assertNotIn("sk-test-value", raw_text)
            self.assertEqual(yaml.safe_load(raw_text)["llm"]["api_key"], "${JOI_LLM_API_KEY}")
            self.assertEqual(yaml.safe_load(raw_text)["characters"][0]["name"], "星野澪")
            self.assertEqual((workspace / "config.yaml").stat().st_mode & 0o777, 0o600)

    def test_rejects_plain_http_remote_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = ByokService(Path(directory), lambda: None)
            result = service.connect(
                {
                    "provider": "openai_compatible",
                    "base_url": "http://remote.example/v1",
                    "model": "example-model",
                    "api_key": "secret-value",
                }
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "invalid_endpoint")
            self.assertFalse((Path(directory) / "config.yaml").exists())

    def test_disconnect_scrubs_legacy_plaintext_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            config_path = workspace / "config.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "llm": {
                            "provider": "openai",
                            "use_mock": False,
                            "base_url": "https://api.openai.com/v1",
                            "model": "gpt-5.6-luna",
                            "api_key": "sk-legacy-plaintext",
                        },
                        "characters": [{"name": "星野澪", "color": "#d76f8f", "setting": "测试角色"}],
                    }
                ),
                encoding="utf-8",
            )
            reloads: list[bool] = []
            service = ByokService(workspace, lambda: reloads.append(True))
            with patch(
                "agent_companion.core.byok.managed_secret_status",
                return_value={"stored": False, "source": "missing", "secure_store_available": True},
            ):
                result = service.disconnect()

            raw_text = config_path.read_text(encoding="utf-8")
            self.assertTrue(result["ok"])
            self.assertNotIn("sk-legacy-plaintext", raw_text)
            self.assertEqual(yaml.safe_load(raw_text)["llm"]["api_key"], "${JOI_LLM_API_KEY}")
            self.assertEqual(reloads, [True])

    def test_connection_test_lists_models_without_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "config.yaml").write_text(
                yaml.safe_dump(
                    {
                        "llm": {
                            "provider": "ollama",
                            "use_mock": False,
                            "base_url": "http://127.0.0.1:11434/v1",
                            "model": "qwen3:8b",
                            "api_key": "ollama",
                        }
                    }
                ),
                encoding="utf-8",
            )
            model_page = SimpleNamespace(data=[SimpleNamespace(id="qwen3:8b")])
            client = MagicMock()
            client.models.list.return_value = model_page
            service = ByokService(workspace, lambda: None)
            with patch("openai.OpenAI", return_value=client):
                result = service.test()

            self.assertTrue(result["ok"])
            self.assertTrue(result["checked_without_generation"])
            self.assertEqual(result["models"], ["qwen3:8b"])
            client.models.list.assert_called_once_with()
            self.assertFalse(client.chat.completions.create.called)

    def test_local_model_discovery_does_not_write_config_or_generate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            client = MagicMock()
            client.models.list.return_value = SimpleNamespace(
                data=[SimpleNamespace(id="qwen3:8b"), SimpleNamespace(id="gemma3:4b")]
            )
            service = ByokService(workspace, lambda: None)
            with patch("openai.OpenAI", return_value=client):
                result = service.discover_models(
                    {"provider": "ollama", "base_url": "http://127.0.0.1:11434/v1"}
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["models"], ["gemma3:4b", "qwen3:8b"])
            self.assertTrue(result["checked_without_generation"])
            self.assertFalse((workspace / "config.yaml").exists())
            client.models.list.assert_called_once_with()
            self.assertFalse(client.chat.completions.create.called)


class ByokConfigTests(unittest.TestCase):
    def test_route_without_secret_inherits_resolved_base_credential(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                yaml.safe_dump(
                    {
                        "llm": {
                            "provider": "openai_compatible",
                            "use_mock": False,
                            "base_url": "https://api.example.com/v1",
                            "model": "base-model",
                            "api_key": "${JOI_LLM_API_KEY}",
                            "routes": {"code": {"model": "code-model"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("agent_companion.core.config.managed_secret", return_value="resolved-system-key"):
                config = load_app_config(path)

            endpoint = ModelRouter(config.llm).resolve("code")
            self.assertEqual(endpoint.api_key, "resolved-system-key")
            self.assertEqual(endpoint.model, "code-model")
            self.assertTrue(endpoint.configured)

    def test_keyring_secret_resolves_environment_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(
                yaml.safe_dump(
                    {
                        "llm": {
                            "provider": "openai",
                            "use_mock": False,
                            "base_url": "https://api.openai.com/v1",
                            "model": "gpt-5.6-luna",
                            "api_key": "${JOI_LLM_API_KEY}",
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch("agent_companion.core.config.managed_secret", return_value="key-from-system-store"):
                config = load_app_config(path)

            self.assertEqual(config.llm.api_key, "key-from-system-store")
            self.assertTrue(config.llm.is_configured)

    def test_unconfigured_chat_explains_next_step_instead_of_echoing_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = CompanionChatTool(Path(directory)).run(ToolRequest("companion.chat", {"text": "你好"}))

            self.assertFalse(result.ok)
            self.assertEqual(result.agent_state["model_error"], "model_unconfigured")
            self.assertIn("BYOK", result.display_card.summary)
            self.assertNotIn("我听到了：你好", result.display_card.summary)

    def test_openai_preset_uses_responses_api(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "config.yaml").write_text(
                yaml.safe_dump(
                    {
                        "llm": {
                            "provider": "openai",
                            "use_mock": False,
                            "base_url": "https://api.openai.com/v1",
                            "model": "gpt-5.6-luna",
                            "api_key": "test-api-key",
                        },
                        "characters": [{"name": "星野澪", "color": "#d76f8f", "setting": "测试角色"}],
                    }
                ),
                encoding="utf-8",
            )
            client = MagicMock()
            client.responses.create.return_value = SimpleNamespace(
                output_text='{"reply":"你好，我在。","voice_text":"你好，我在。","emotion":"happy","sprite":"1"}'
            )
            with (
                patch("agent_companion.core.config.managed_secret", return_value=""),
                patch("openai.OpenAI", return_value=client),
            ):
                result = CompanionChatTool(workspace).run(ToolRequest("companion.chat", {"text": "你好"}))

            self.assertTrue(result.ok)
            self.assertEqual(result.display_card.summary, "你好，我在。")
            client.responses.create.assert_called_once()
            self.assertFalse(client.chat.completions.create.called)


if __name__ == "__main__":
    unittest.main()
