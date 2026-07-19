from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from agent_companion.core.agent_cli import _discover_codex_models
from agent_companion.core.codex_runtime import _codex_runtime_overrides


class CodexModelDiscoveryTests(unittest.TestCase):
    def test_reads_visible_models_and_preserves_cli_slugs(self) -> None:
        catalog = {
            "models": [
                {
                    "slug": "gpt-5.5",
                    "display_name": "GPT-5.5",
                    "visibility": "list",
                    "priority": 7,
                    "default_reasoning_level": "medium",
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
                },
                {
                    "slug": "gpt-5.6-sol",
                    "display_name": "GPT-5.6-Sol",
                    "visibility": "list",
                    "priority": 1,
                    "default_reasoning_level": "low",
                    "supported_reasoning_levels": [{"effort": "low"}, {"effort": "ultra"}],
                },
                {"slug": "hidden-model", "display_name": "Hidden", "visibility": "hide", "priority": 0},
            ]
        }
        completed = subprocess.CompletedProcess(["codex", "debug", "models"], 0, json.dumps(catalog), "")

        with patch("agent_companion.core.agent_cli.subprocess.run", return_value=completed) as run:
            result = _discover_codex_models("/safe/codex")

        run.assert_called_once_with(["/safe/codex", "debug", "models"], capture_output=True, text=True, timeout=12)
        self.assertEqual(result["models_source"], "cli_live")
        self.assertEqual(result["models"], ["默认", "gpt-5.6-sol", "gpt-5.5"])
        self.assertEqual(result["model_options"][1]["label"], "GPT-5.6-Sol")
        self.assertEqual(result["model_options"][1]["reasoning"], ["low", "ultra"])
        self.assertEqual(result["model_options"][1]["default_reasoning"], "low")

    def test_invalid_catalog_falls_back_without_stale_models(self) -> None:
        completed = subprocess.CompletedProcess(["codex", "debug", "models"], 0, "not-json", "")

        with patch("agent_companion.core.agent_cli.subprocess.run", return_value=completed):
            result = _discover_codex_models("/safe/codex")

        self.assertEqual(result["models"], ["默认"])
        self.assertEqual(result["models_source"], "fallback")
        self.assertEqual(result["models_error"], "model_catalog_invalid")

    def test_timeout_falls_back_safely(self) -> None:
        with patch("agent_companion.core.agent_cli.subprocess.run", side_effect=subprocess.TimeoutExpired("codex", 12)):
            result = _discover_codex_models("/safe/codex")

        self.assertEqual(result["models"], ["默认"])
        self.assertEqual(result["models_error"], "model_catalog_timeout")

    def test_selected_model_and_reasoning_become_codex_arguments(self) -> None:
        self.assertEqual(
            _codex_runtime_overrides("gpt-5.6-sol", "XHigh"),
            ["--model", "gpt-5.6-sol", "--config", 'model_reasoning_effort="xhigh"'],
        )
        self.assertEqual(_codex_runtime_overrides("默认", "默认"), [])
        self.assertEqual(_codex_runtime_overrides("bad model", "unexpected"), [])


if __name__ == "__main__":
    unittest.main()
