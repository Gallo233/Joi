from __future__ import annotations

from typing import Any

from agent_companion.core.character_motion import MOTION_SPECS, character_motion_payload
from agent_companion.core.schemas import DisplayCard, ToolRequest, ToolResult
from agent_companion.core.tools.base import ToolAdapter
from agent_companion.core.voice import safe_voice_line


class CharacterPerformTool(ToolAdapter):
    """Emit a safe semantic motion request for the local character renderer."""

    name = "character.perform"

    def __init__(self, character: Any = None) -> None:
        # Optional so the tool stays constructible on its own in tests and in
        # a workspace with no character configured at all.
        self._character = character

    def _line_for(self, motion: str, spec, reply_language: str = "") -> str:
        """What the character says while doing this, in her own words if she has any.

        The shared table is Joi's voice, not the character's: "好耶。" is fine
        for a default mascot and wrong for one written as quiet and sparing. A
        package that declares `motion_lines` speaks for itself; one that does
        not keeps the table, so nothing regresses by staying silent about it.

        The language is the user's, not the character's. Which language she
        *speaks* is a separate setting; asking "跳个舞" in Chinese and being
        answered in Japanese on screen is the same violation the chat replies
        already avoid.
        """

        chooser = getattr(self._character, "motion_line", None)
        authored = chooser(motion, reply_language) if callable(chooser) else ""
        return authored or spec.voice

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
        spoken = safe_voice_line(
            self._line_for(motion["name"], spec, str(request.arguments.get("reply_language") or "")),
            emotion=spec.emotion,
            sprite="5" if spec.emotion == "happy" else "1",
        )
        return ToolResult(
            ok=True,
            agent_state={
                "tool": self.name,
                "character_motion": motion,
            },
            # The summary is rendered as the character's own speech bubble, so
            # it has to be what the character says -- not a description of her
            # in the third person, and not under a name that is Joi's rather
            # than hers. She was heard saying one thing and shown saying
            # another, which reads as the app talking over her.
            display_card=DisplayCard(
                "角色动作",
                spoken.text,
                f"本地{spec.label}表现，可被下一条用户输入或角色动作立即打断。",
                status="success",
            ),
            voice_line=spoken,
        )
