from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Mapping

from agent_companion.core.minecraft_contract import MinecraftContractError, canonicalize_game_intent


# Scheme A: autonomy may propose everything the scope allows except attack,
# which is the one primitive that actively harms an entity.
_AUTONOMY_BLOCKED_ACTIONS = frozenset({"attack"})
_MIN_INTERVAL_SECONDS = 10.0
_MAX_INTERVAL_SECONDS = 300.0
_DEFAULT_INTERVAL_SECONDS = 20.0
_MAX_DECISION_TEXT = 400

AutonomyProposer = Callable[[str], Any]
AutonomySubmit = Callable[[str, dict[str, Any]], dict[str, Any]]
SessionContext = Callable[[str], dict[str, Any]]
AutonomySpeakSink = Callable[[str], None]
SpeechGuard = Callable[[], bool]


class MinecraftAutonomyTicker:
    """Bounded, user-preemptible autonomy for one active Minecraft session.

    Review rules baked in:
    - B4: one tick per interval; a busy or non-running session is skipped,
      never queued; the service cancels an autonomy goal when the user submits
      their own (see ``MinecraftGameService.submit_goal(autonomy=...)``).
    - C1: autonomy shares the user-confirmed action/block budget (no separate
      quota in v1); the scope approval text says so.
    - Scheme A: ``attack`` is rejected here and again by the service.
    - Speaker priority: a speak is skipped while the user's own turn still owns
      the voice channel (the guard callback), so Joi never talks over a reply.
    """

    def __init__(
        self,
        *,
        propose: AutonomyProposer,
        submit: AutonomySubmit,
        session_context: SessionContext,
        on_speak: AutonomySpeakSink,
        speech_guard: SpeechGuard | None = None,
        interval_seconds: float = _DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        self._propose = propose
        self._submit = submit
        self._context = session_context
        self._on_speak = on_speak
        self._speech_guard = speech_guard or (lambda: False)
        self.interval_seconds = _clamped_interval(interval_seconds)
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._stops: dict[str, threading.Event] = {}
        self._stats: dict[str, dict[str, Any]] = {}

    def start(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._threads:
                return False
            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(session_id, stop_event),
                name=f"minecraft-autonomy-{session_id[-8:]}",
                daemon=True,
            )
            self._threads[session_id] = thread
            self._stops[session_id] = stop_event
            self._stats[session_id] = {"ticks": 0, "speaks": 0, "proposals": 0, "skips": 0, "last_error": ""}
            thread.start()
            return True

    def stop(self, session_id: str) -> None:
        with self._lock:
            stop_event = self._stops.pop(session_id, None)
            thread = self._threads.pop(session_id, None)
        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=2.0)
        # After the join: a tick still finishing would otherwise recreate the row
        # it is counting into, and leave it behind for a session that is over.
        with self._lock:
            self._stats.pop(session_id, None)

    def stop_all(self) -> None:
        with self._lock:
            session_ids = list(self._threads)
        for session_id in session_ids:
            self.stop(session_id)

    def set_interval(self, interval_seconds: float) -> None:
        self.interval_seconds = _clamped_interval(interval_seconds)

    def status(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            stats = self._stats.get(session_id)
            return {
                "ok": session_id in self._threads,
                "running": session_id in self._threads,
                "interval_seconds": self.interval_seconds,
                "stats": dict(stats) if stats else {},
            }

    def _run(self, session_id: str, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            stop_event.wait(self.interval_seconds)
            if stop_event.is_set():
                return
            try:
                self._tick(session_id)
            except Exception as exc:
                with self._lock:
                    row = self._stats.get(session_id)
                    if row is not None:
                        row["last_error"] = f"{type(exc).__name__}"[:80]

    def _tick(self, session_id: str) -> None:
        with self._lock:
            row = self._stats.setdefault(session_id, {"ticks": 0, "speaks": 0, "proposals": 0, "skips": 0, "last_error": ""})
            row["ticks"] += 1
        context = _safe_context(self._context(session_id))
        if not context.get("ok"):
            self._count(session_id, "skips")
            return
        decision = _parse_decision(self._propose(_build_prompt(context)))
        kind = str(decision.get("kind") or "none")
        if kind == "speak":
            text = str(decision.get("text") or "").strip()
            if not text or self._speech_guard():
                self._count(session_id, "skips")
                return
            self._on_speak(text[: _MAX_DECISION_TEXT])
            self._count(session_id, "speaks")
            return
        if kind == "propose":
            # While a goal is running the tick exists so Joi can talk about it;
            # a second action would only be refused by the service anyway.
            if context.get("busy"):
                self._count(session_id, "skips")
                return
            intent = _canonical_intent(decision.get("intent"))
            if intent is None or str(intent.get("action") or "") in _AUTONOMY_BLOCKED_ACTIONS:
                self._count(session_id, "skips")
                return
            result = self._submit(session_id, intent)
            if bool(result.get("ok")):
                self._count(session_id, "proposals")
            else:
                self._count(session_id, "skips")
            return
        self._count(session_id, "skips")

    def _count(self, session_id: str, field: str) -> None:
        with self._lock:
            row = self._stats.get(session_id)
            if row is not None and field in row:
                row[field] += 1


def _clamped_interval(value: Any) -> float:
    try:
        interval = float(value)
    except (TypeError, ValueError):
        interval = _DEFAULT_INTERVAL_SECONDS
    return max(_MIN_INTERVAL_SECONDS, min(interval, _MAX_INTERVAL_SECONDS))


def _safe_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"ok": False}
    return {str(key): item for key, item in value.items()}


def _build_prompt(context: Mapping[str, Any]) -> str:
    observation = context.get("observation") if isinstance(context.get("observation"), Mapping) else {}
    hostiles = context.get("hostiles") if isinstance(context.get("hostiles"), list) else []
    recent_events = context.get("recent_event_types") if isinstance(context.get("recent_event_types"), list) else []
    screen_text = str(context.get("screen_text") or "")[:600]
    persona = str(context.get("persona") or "")[:800]
    language = str(context.get("language") or "").strip()[:40]
    busy = bool(context.get("busy"))
    players = [str(name)[:32] for name in (context.get("allowed_players") or []) if str(name).strip()][:8]
    inventory = observation.get("inventory_items") if isinstance(observation.get("inventory_items"), list) else []
    inventory_line = "、".join(
        f"{row.get('name')}×{row.get('count')}" for row in inventory[:12] if isinstance(row, Mapping)
    ) or "空"
    blocks = observation.get("nearby_blocks") if isinstance(observation.get("nearby_blocks"), list) else []
    blocks_line = "、".join(
        f"{row.get('name')}×{row.get('count')}（{row.get('distance')}·{row.get('direction')}）"
        for row in blocks[:8]
        if isinstance(row, Mapping)
    ) or "没看到什么特别的"
    hostile_line = "、".join(f"{row.get('name')}×{row.get('count')}" for row in hostiles[:8]) or "无"
    events_line = "、".join(str(item)[:40] for item in recent_events[-8:]) or "无"
    memory = str(context.get("memory") or "")[:500]
    # Autonomy speaks straight into the chat and the character voice with no
    # language rule anywhere downstream, so a Japanese-named character answered a
    # Chinese session in Japanese. The rule has to be in the prompt that writes
    # the line, exactly as the realtime instructions carry it.
    language_rule = f"台词必须用{language}书写；即使角色设定是别的语言，也不要切换。" if language else ""
    return "\n".join(
        [
            "你是 Joi，正在 Minecraft 里自主观察。基于下面的 sanitized 状态，决定此刻是否要做点什么。",
            "输出必须是严格的 JSON 对象，三选一：",
            '{"kind":"speak","text":"一句自然、适合直接朗读的台词或对用户的提问"}',
            '{"kind":"propose","intent":{...一个合法的游戏动作...}}',
            '{"kind":"none"}',
            (
                "你现在正在执行一个动作，本轮只能 speak（说说你在做什么、进度如何、接下来打算怎么办），不要 propose。"
                if busy
                else "规则：台词要简短自然、符合角色；propose 只能是 observe/inventory/follow_player/come_to_player/collect/mine/craft/eat/place_blueprint/deposit/flee/guard 之一；"
            ),
            (
                ""
                if busy
                else "优先做有用的事：背包空就去采集允许的方块，材料够了就合成，饿了就吃东西，"
                "有敌对生物就 flee 或 guard，玩家离得远就 come_to_player。没有值得做的事才输出 none。"
            ),
            "绝对不要 propose attack；不要输出坐标、路径、标识符或 JSON 以外的任何内容。",
            language_rule,
            f"世界状态：{observation}",
            f"背包：{inventory_line}",
            f"周围：{blocks_line}",
            f"可跟随的玩家：{'、'.join(players) or '无'}",
            f"附近敌对生物：{hostile_line}",
            f"最近事件：{events_line}",
            f"屏幕摘要：{screen_text or '无'}",
            f"世界记忆：{memory or '无'}",
            f"角色设定：{persona or 'Joi'}",
        ]
    )


def _parse_decision(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        parsed = dict(raw)
    else:
        try:
            loaded = json.loads(str(raw or ""))
        except (TypeError, ValueError):
            return {"kind": "none"}
        if not isinstance(loaded, dict):
            return {"kind": "none"}
        parsed = loaded
    kind = str(parsed.get("kind") or "none")
    if kind not in {"speak", "propose", "none"}:
        return {"kind": "none"}
    decision: dict[str, Any] = {"kind": kind}
    if kind == "speak":
        decision["text"] = _bounded_plain(parsed.get("text"), _MAX_DECISION_TEXT)
    if kind == "propose":
        decision["intent"] = parsed.get("intent")
    return decision


def _canonical_intent(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    try:
        return canonicalize_game_intent({"final": True, "source": "voice", "intent": dict(raw)})
    except MinecraftContractError:
        return None


def _bounded_plain(value: Any, limit: int) -> str:
    text = str(value or "")
    text = "".join(char for char in text if char in "\n\t" or ord(char) >= 32)
    return text.strip()[:limit]
