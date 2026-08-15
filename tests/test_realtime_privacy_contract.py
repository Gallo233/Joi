from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RealtimePrivacyContractTests(unittest.TestCase):
    def test_old_browser_provider_transport_is_not_callable(self) -> None:
        shell = (ROOT / "agent_companion" / "shell" / "src" / "realtimeVoice.ts").read_text(encoding="utf-8")
        api = (ROOT / "agent_companion" / "shell" / "src" / "api.ts").read_text(encoding="utf-8")
        server = (ROOT / "agent_companion" / "core" / "server.py").read_text(encoding="utf-8")
        combined = shell + api + server
        for forbidden in ("RTCPeerConnection", "createDataChannel", "voice.realtime.call", "exchangeSdp"):
            self.assertNotIn(forbidden, combined)

    def test_release_seed_excludes_raw_node_modules_and_secret_files(self) -> None:
        builder = (ROOT / "tools" / "build_core_sidecar.py").read_text(encoding="utf-8")
        config = (ROOT / "agent_companion" / "shell" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
        self.assertIn('shutil.ignore_patterns("node_modules"', builder)
        self.assertNotIn("secrets.yaml", builder)
        self.assertNotIn("默认业务空间", builder + config)

    def test_shell_has_no_qwen_credential_field_or_provider_endpoint(self) -> None:
        shell_sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "agent_companion" / "shell" / "src").rglob("*")
            if path.suffix in {".ts", ".vue"}
        )
        self.assertNotIn("JOI_QWEN_REALTIME_API_KEY", shell_sources)
        self.assertNotIn("dashscope.aliyuncs.com/api-ws", shell_sources)
        self.assertNotIn("Authorization: Bearer", shell_sources)


if __name__ == "__main__":
    unittest.main()
