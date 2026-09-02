from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_companion.core.character import CharacterHarness
from agent_companion.core.config import AppConfig
from agent_companion.core.tools.agent_cli import AgentCliRunTool
from agent_companion.core.tools.browser import BrowserTool
from agent_companion.core.tools.chat import CompanionChatTool
from agent_companion.core.tools.character import CharacterPerformTool
from agent_companion.core.tools.codex import CodexTool
from agent_companion.core.tools.computer import ComputerActionTool
from agent_companion.core.tools.desktop_workflow import DesktopWorkflowTool
from agent_companion.core.tools.files import FileReadTool
from agent_companion.core.tools.game_ok_ww import OkWwTool
from agent_companion.core.tools.mcp import McpListTool
from agent_companion.core.tools.registry import ToolRegistry
from agent_companion.core.tools.runtime_config import RuntimeConfigUpdateTool
from agent_companion.core.tools.screen_observe import ScreenObserveTool
from agent_companion.core.tools.targeting import (
    SemanticTargetSelectionStore,
    SemanticTargetSelectionTool,
    SemanticTargetTool,
)
from agent_companion.core.tools.watch import WatchRecallTool
from agent_companion.core.vision.ocr import PytesseractOcrExtractor
from agent_companion.core.vision.summarizer import OpenAIVisionSummarizer
from agent_companion.core.watch import WatchAnswerer, WatchSession
from agent_companion.core.watch_transcript import SystemAudioTranscriptProvider

if TYPE_CHECKING:
    from agent_companion.core.computer_use import ComputerUseBackend


def build_tool_registry(
    workspace: Path,
    *,
    config: AppConfig | None,
    character: CharacterHarness,
    watch_session: WatchSession,
    semantic_selection: SemanticTargetSelectionStore,
    ocr: PytesseractOcrExtractor,
    vision_summarizer: OpenAIVisionSummarizer | None,
    audio_transcriber: SystemAudioTranscriptProvider,
    computer_backend: ComputerUseBackend | None = None,
) -> ToolRegistry:
    """Build the default tool adapter set outside the application runtime."""

    registry = ToolRegistry()
    post_action_settle_ms = config.computer_use.post_action_settle_ms if config else 200
    registry.register(CompanionChatTool(workspace))
    registry.register(CharacterPerformTool(config.primary_character if config and config.characters else None))
    registry.register(AgentCliRunTool(workspace))
    registry.register(CodexTool(workspace))
    registry.register(BrowserTool(workspace, "browser.search"))
    registry.register(BrowserTool(workspace, "browser.observe"))
    registry.register(
        ScreenObserveTool(
            workspace,
            summarizer=vision_summarizer,
            ocr=ocr,
            audio_transcriber=audio_transcriber,
            computer_backend=computer_backend,
        )
    )
    registry.register(
        WatchRecallTool(
            workspace,
            watch_session.recent_with_transcript,
            WatchAnswerer(workspace, character.name, character.persona),
        )
    )
    registry.register(SemanticTargetTool(workspace, ocr=ocr, computer_backend=computer_backend))
    registry.register(SemanticTargetSelectionTool(semantic_selection))
    for name, action_type in (
        ("computer.click", "click"),
        ("computer.double_click", "double_click"),
        ("computer.drag", "drag"),
        ("computer.open_app", "open_app"),
        ("computer.type_text", "type_text"),
        ("computer.scroll", "scroll"),
        ("computer.hotkey", "hotkey"),
    ):
        registry.register(
            ComputerActionTool(
                workspace,
                name,
                action_type,
                backend=computer_backend,
                ocr=ocr,
                post_action_settle_ms=post_action_settle_ms,
            )
        )
    registry.register(
        DesktopWorkflowTool(
            workspace,
            backend=computer_backend,
            ocr=ocr,
            post_action_settle_ms=max(post_action_settle_ms, 600),
        )
    )
    registry.register(OkWwTool(workspace))
    registry.register(McpListTool(workspace))
    registry.register(FileReadTool(workspace))
    registry.register(RuntimeConfigUpdateTool(workspace))
    return registry
