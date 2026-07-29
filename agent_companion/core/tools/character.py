from __future__ import annotations

from agent_companion.core.character_motion import MOTION_SPECS, character_motion_payload
from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class CharacterPerformTool(ToolAdapter):
    """Emit a safe semantic motion request for the local character renderer."""

    name = "character.perform"

    def run(self, request: ToolRequest) -> ToolResult:
        motion = character_motion_payload(
            request.arguments.get("motion"),
            duration_ms=request.arguments.get("duration_ms"),
            loop=request.arguments.get("loop"),
            intensity=request.arguments.get("intensity"),
        )
        if motion is None:
            return ToolResult(
                ok=False,
                agent_state={"tool": self.name, "error": "unsupported_motion"},
                display_card=DisplayCard("角色动作", "这个动作还没有准备好。", status="failed"),
                voice_line=safe_voice_line("这个动作我还没学会。", emotion="worried", sprite="4"),
            )

        spec = MOTION_SPECS[motion["name"]]
        return ToolResult(
            ok=True,
            agent_state={
                "tool": self.name,
                "character_motion": motion,
            },
            display_card=DisplayCard(
                "角色动作",
                f"Joi 开始{spec.label}。",
                "这是本地角色表现，可被下一条用户输入或角色动作立即打断。",
                status="success",
            ),
            voice_line=safe_voice_line(spec.voice, emotion=spec.emotion, sprite="5" if spec.emotion == "happy" else "1"),
        )
