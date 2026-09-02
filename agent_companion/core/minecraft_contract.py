from __future__ import annotations

import re
from typing import Any, Mapping


MINECRAFT_PROTOCOL = "joi.game_adapter"
MINECRAFT_PROTOCOL_VERSION = 2
GAME_ACTIONS = frozenset(
    {
        "observe",
        "inventory",
        "follow_player",
        "come_to_player",
        "collect",
        "mine",
        "craft",
        "eat",
        "place_blueprint",
        "deposit",
        "attack",
        "flee",
        "guard",
        "smelt",
        "sort_inventory",
        "equip",
        "drop",
        "fish",
        "sleep",
    }
)
# Read-only lookups answered from game data. They change nothing, so they are
# cheap to allow and are what stops the model guessing recipes and directions.
QUERY_ACTIONS = frozenset({"inspect_container", "lookup_recipe", "locate", "load_skill"})
# Core-executed read-only action: the bridge never sees it (the bridge cannot
# see the screen). It shares the same canonicalize/gate/budget/receipt chain.
SCREEN_ACTIONS = frozenset({"observe_screen"})
DANGEROUS_BLOCKS = frozenset(
    {
        "tnt",
        "fire",
        "lava",
        "lava_bucket",
        "end_crystal",
        "respawn_anchor",
        "bedrock",
        "command_block",
        "chain_command_block",
        "repeating_command_block",
        "structure_block",
        "jigsaw",
    }
)

_IDENTIFIER = re.compile(r"^[a-z0-9_:.\-/]{1,80}$")
_PLAYER = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_FORBIDDEN_CODE_KEYS = frozenset(
    {
        "code",
        "command",
        "commands",
        "eval",
        "javascript",
        "js",
        "lua",
        "python",
        "script",
        "shell",
    }
)
_ABSOLUTE_COORDINATES = frozenset({"x", "y", "z", "position", "coordinates", "origin"})
_DIMENSIONS = frozenset({"overworld", "the_nether", "the_end"})


class MinecraftContractError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonicalize_game_intent(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate the Core input boundary and return one strict P2 intent.

    Finality is deliberately part of this boundary instead of a caller
    convention: partial ASR hypotheses can never become game actions.
    """

    wrapper = _mapping(payload, "invalid_game_intent")
    _exact_fields(wrapper, {"final", "source", "intent"}, set(), "unexpected_intent_envelope_field")
    if wrapper.get("final") is not True:
        raise MinecraftContractError("final_transcript_required")
    if wrapper.get("source") not in {"voice", "text"}:
        raise MinecraftContractError("invalid_intent_source")
    raw = _mapping(wrapper.get("intent"), "invalid_game_intent")
    _reject_code_fields(raw)
    action = str(raw.get("action") or "")
    if action not in GAME_ACTIONS | SCREEN_ACTIONS | QUERY_ACTIONS:
        raise MinecraftContractError("unknown_game_action")

    builders = {
        "observe": _observe,
        "inventory": _inventory,
        "observe_screen": _observe_screen,
        "follow_player": _follow,
        "come_to_player": _come,
        "collect": lambda value: _block_action(value, "collect"),
        "mine": lambda value: _block_action(value, "mine"),
        "craft": _craft,
        "eat": _eat,
        "place_blueprint": _blueprint,
        "deposit": _deposit,
        "attack": _attack,
        "flee": _flee,
        "guard": _guard,
        "smelt": _smelt,
        "sort_inventory": _sort_inventory,
        "equip": _equip,
        "drop": _drop,
        "fish": _fish,
        "sleep": _sleep,
        "inspect_container": _inspect_container,
        "lookup_recipe": _lookup_recipe,
        "locate": _locate,
        "load_skill": _load_skill,
    }
    return builders[action](raw)


def canonicalize_minecraft_scope(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = _mapping(payload, "invalid_minecraft_scope")
    required = {
        "server_id",
        "world",
        "dimensions",
        "max_radius",
        "max_actions",
        "max_blocks_changed",
        "allowed_blocks",
    }
    optional = {"allowed_players", "allow_build", "allow_containers"}
    _exact_fields(raw, required, optional, "unexpected_minecraft_scope_field", missing_code="incomplete_minecraft_scope")
    server_id = _identifier(raw.get("server_id"), "invalid_server_id")
    world = _identifier(raw.get("world"), "invalid_world")
    dimensions_raw = raw.get("dimensions")
    if not isinstance(dimensions_raw, list) or not dimensions_raw or len(dimensions_raw) > 3:
        raise MinecraftContractError("invalid_dimensions")
    dimensions = list(dict.fromkeys(str(item) for item in dimensions_raw))
    if any(item not in _DIMENSIONS for item in dimensions):
        raise MinecraftContractError("invalid_dimensions")
    allowed_blocks_raw = raw.get("allowed_blocks")
    if not isinstance(allowed_blocks_raw, list) or not allowed_blocks_raw or len(allowed_blocks_raw) > 128:
        raise MinecraftContractError("invalid_allowed_blocks")
    allowed_blocks = list(dict.fromkeys(_block_identifier(item, "invalid_allowed_block") for item in allowed_blocks_raw))
    players_raw = raw.get("allowed_players", [])
    if not isinstance(players_raw, list) or len(players_raw) > 16:
        raise MinecraftContractError("invalid_allowed_players")
    players = list(dict.fromkeys(_player(item) for item in players_raw))
    return {
        "server_id": server_id,
        "world": world,
        "dimensions": dimensions,
        "max_radius": _integer(raw.get("max_radius"), 4, 128, "invalid_scope_radius"),
        "max_actions": _integer(raw.get("max_actions"), 1, 200, "invalid_scope_action_budget"),
        "max_blocks_changed": _integer(raw.get("max_blocks_changed"), 0, 512, "invalid_scope_block_budget"),
        "allowed_blocks": allowed_blocks,
        "allowed_players": players,
        "allow_build": _boolean(raw.get("allow_build", False), "invalid_allow_build"),
        "allow_containers": _boolean(raw.get("allow_containers", False), "invalid_allow_containers"),
    }


def _player_in_scope(player: Any, scope: Mapping[str, Any]) -> bool:
    """Match an allowed player the way Minecraft names are actually typed.

    Case-sensitively, this denied "steve" against an allowed "Steve" -- and the
    in-game chat gate right next to it already compared casefolded, so the two
    doors into the same permission disagreed about who was through it.
    """

    name = str(player or "").strip().casefold()
    return bool(name) and name in {str(row).strip().casefold() for row in (scope.get("allowed_players") or [])}


def check_intent_scope(intent: Mapping[str, Any], scope: Mapping[str, Any]) -> str:
    """Return an audit-safe denial code, or an empty string when in scope."""

    dimension = str(intent.get("dimension") or "")
    if dimension and dimension not in set(scope.get("dimensions") or []):
        return "dimension_out_of_scope"
    radius = int(intent.get("radius") or 0)
    if radius and radius > int(scope.get("max_radius") or 0):
        return "radius_out_of_scope"
    action = str(intent.get("action") or "")
    block = str(intent.get("block") or "")
    allowed_blocks = set(scope.get("allowed_blocks") or [])
    if action in {"collect", "mine"} and block not in allowed_blocks:
        return "block_out_of_scope"
    if action in {"follow_player", "come_to_player"} and not _player_in_scope(intent.get("player"), scope):
        return "player_out_of_scope"
    if action == "place_blueprint":
        if not bool(scope.get("allow_build")):
            return "building_not_allowed"
        if intent.get("anchor") == "player" and not _player_in_scope(intent.get("player"), scope):
            return "player_out_of_scope"
        for row in intent.get("blocks") or []:
            if str(row.get("block") or "") not in allowed_blocks:
                return "block_out_of_scope"
            if max(abs(int(value)) for value in row.get("offset") or [0, 0, 0]) > int(scope.get("max_radius") or 0):
                return "radius_out_of_scope"
    # Anything that puts items into, or reads items out of, someone's storage is
    # the container permission -- the same one deposit has always needed.
    if action in {"deposit", "sort_inventory", "inspect_container"} and not bool(scope.get("allow_containers")):
        return "containers_not_allowed"
    # Smelting consumes what the user confirmed Joi may handle, so the input and
    # any named fuel are held to the same allow-list as mining and collecting.
    if action == "smelt":
        for item in (str(intent.get("item") or ""), str(intent.get("fuel") or "")):
            if item and item not in allowed_blocks:
                return "block_out_of_scope"
    # Dropping is the one way items leave Joi's hands for good; only things the
    # user put in scope may be thrown away.
    if action == "drop" and str(intent.get("item") or "") not in allowed_blocks:
        return "block_out_of_scope"
    if estimated_world_changes(intent) > int(scope.get("max_blocks_changed") or 0):
        return "block_budget_exceeded"
    return ""


def estimated_world_changes(intent: Mapping[str, Any]) -> int:
    """A floor, reserved before the action runs -- never the final account.

    Only what the action sets out to change can be known in advance. Walking to
    the work can break and place blocks of its own, so the receipt reports what
    the world actually changed and the session budget is settled against that.
    """

    action = str(intent.get("action") or "")
    if action in {"collect", "mine"}:
        return int(intent.get("count") or 0)
    if action == "place_blueprint":
        return len(intent.get("blocks") or [])
    return 0


def _observe(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, {"dimension", "radius"}, "unexpected_intent_field")
    return {
        "action": "observe",
        "dimension": _dimension(raw.get("dimension", "overworld")),
        "radius": _integer(raw.get("radius", 16), 1, 32, "invalid_observe_radius"),
    }


def _observe_screen(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, set(), "unexpected_intent_field")
    return {"action": "observe_screen"}


def _attack(raw: Mapping[str, Any]) -> dict[str, Any]:
    # No player/entity name field exists: the target is always resolved by the
    # bridge as the nearest hostile mob within radius, so a player target
    # cannot even be expressed here.
    _exact_fields(raw, {"action"}, {"count", "radius", "dimension"}, "unexpected_intent_field")
    return {
        "action": "attack",
        # One goal reserves one action from the user's budget, so an unbounded
        # count would turn a single confirmed instruction into a long fight.
        "count": _integer(raw.get("count", 1), 1, 16, "invalid_attack_count"),
        "radius": _integer(raw.get("radius", 16), 1, 32, "invalid_attack_radius"),
        "dimension": _dimension(raw.get("dimension", "overworld")),
    }


def _flee(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, {"distance", "duration_seconds", "dimension"}, "unexpected_intent_field")
    return {
        "action": "flee",
        "distance": _integer(raw.get("distance", 12), 4, 32, "invalid_flee_distance"),
        "duration_seconds": _integer(raw.get("duration_seconds", 15), 1, 120, "invalid_flee_duration"),
        "dimension": _dimension(raw.get("dimension", "overworld")),
    }


def _guard(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, {"dimension"}, "unexpected_intent_field")
    return {"action": "guard", "dimension": _dimension(raw.get("dimension", "overworld"))}


def _inventory(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, set(), "unexpected_intent_field")
    return {"action": "inventory"}


def _follow(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "player"}, {"distance", "duration_seconds"}, "unexpected_intent_field")
    return {
        "action": "follow_player",
        "player": _player(raw.get("player")),
        "distance": _integer(raw.get("distance", 3), 2, 12, "invalid_follow_distance"),
        "duration_seconds": _integer(raw.get("duration_seconds", 30), 1, 300, "invalid_follow_duration"),
    }


def _come(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "player"}, {"distance"}, "unexpected_intent_field")
    return {
        "action": "come_to_player",
        "player": _player(raw.get("player")),
        "distance": _integer(raw.get("distance", 2), 1, 12, "invalid_follow_distance"),
    }


def _smelt(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "item"}, {"count", "fuel"}, "unexpected_intent_field")
    return {
        "action": "smelt",
        "item": _block_identifier(raw.get("item"), "invalid_item"),
        "count": _integer(raw.get("count", 1), 1, 64, "invalid_smelt_count"),
        "fuel": _block_identifier(raw.get("fuel"), "invalid_fuel") if raw.get("fuel") else "",
    }


def _sort_inventory(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, {"container", "radius", "dimension", "keep"}, "unexpected_intent_field")
    keep_raw = raw.get("keep", [])
    if not isinstance(keep_raw, list) or len(keep_raw) > 16:
        raise MinecraftContractError("invalid_keep_items")
    intent = {
        "action": "sort_inventory",
        "container": _enum(raw.get("container", "chest"), {"chest", "barrel", "shulker_box"}, "invalid_container"),
        "radius": _integer(raw.get("radius", 8), 1, 16, "invalid_scope_radius"),
        # What Joi keeps on her rather than storing: tools she is using, food.
        "keep": [_block_identifier(item, "invalid_item") for item in keep_raw],
    }
    _apply_dimension(raw, intent)
    return intent


def _equip(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "item"}, {"destination"}, "unexpected_intent_field")
    return {
        "action": "equip",
        "item": _block_identifier(raw.get("item"), "invalid_item"),
        "destination": _enum(
            raw.get("destination", "hand"),
            {"hand", "off-hand", "head", "torso", "legs", "feet"},
            "invalid_equip_destination",
        ),
    }


def _drop(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "item"}, {"count"}, "unexpected_intent_field")
    return {
        "action": "drop",
        "item": _block_identifier(raw.get("item"), "invalid_item"),
        "count": _integer(raw.get("count", 1), 1, 64, "invalid_drop_count"),
    }


def _fish(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, {"duration_seconds", "dimension"}, "unexpected_intent_field")
    intent = {
        "action": "fish",
        "duration_seconds": _integer(raw.get("duration_seconds", 60), 1, 300, "invalid_fish_duration"),
    }
    _apply_dimension(raw, intent)
    return intent


def _sleep(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, {"radius", "dimension"}, "unexpected_intent_field")
    intent = {"action": "sleep", "radius": _integer(raw.get("radius", 8), 1, 16, "invalid_scope_radius")}
    _apply_dimension(raw, intent)
    return intent


def _inspect_container(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, {"container", "radius", "dimension"}, "unexpected_intent_field")
    intent = {
        "action": "inspect_container",
        "container": _enum(
            raw.get("container", "chest"),
            {"chest", "barrel", "shulker_box", "furnace", "blast_furnace", "smoker"},
            "invalid_container",
        ),
        "radius": _integer(raw.get("radius", 8), 1, 16, "invalid_scope_radius"),
    }
    _apply_dimension(raw, intent)
    return intent


def _lookup_recipe(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "item"}, set(), "unexpected_intent_field")
    return {"action": "lookup_recipe", "item": _block_identifier(raw.get("item"), "invalid_item")}


def _locate(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "target"}, {"kind", "dimension"}, "unexpected_intent_field")
    intent = {
        "action": "locate",
        "kind": _enum(raw.get("kind", "structure"), {"structure", "biome"}, "invalid_locate_kind"),
        "target": _block_identifier(raw.get("target"), "invalid_locate_target"),
    }
    _apply_dimension(raw, intent)
    return intent


def _load_skill(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "name"}, set(), "unexpected_intent_field")
    name = str(raw.get("name") or "").strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", name):
        raise MinecraftContractError("invalid_skill_name")
    return {"action": "load_skill", "name": name}


def _enum(value: Any, allowed: set[str], code: str) -> str:
    text = str(value or "")
    if text not in allowed:
        raise MinecraftContractError(code)
    return text


def _apply_dimension(raw: Mapping[str, Any], intent: dict[str, Any]) -> None:
    if raw.get("dimension") is not None:
        dimension = str(raw.get("dimension") or "")
        if dimension not in _DIMENSIONS:
            raise MinecraftContractError("invalid_dimension")
        intent["dimension"] = dimension


def _block_action(raw: Mapping[str, Any], action: str) -> dict[str, Any]:
    _exact_fields(raw, {"action", "block"}, {"count", "radius", "dimension"}, "unexpected_intent_field")
    return {
        "action": action,
        "block": _block_identifier(raw.get("block"), "invalid_block"),
        "count": _integer(raw.get("count", 1), 1, 64, "invalid_block_count"),
        "radius": _integer(raw.get("radius", 32), 1, 64, "invalid_action_radius"),
        "dimension": _dimension(raw.get("dimension", "overworld")),
    }


def _craft(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "item"}, {"count"}, "unexpected_intent_field")
    return {
        "action": "craft",
        "item": _identifier(raw.get("item"), "invalid_item"),
        "count": _integer(raw.get("count", 1), 1, 64, "invalid_item_count"),
    }


def _eat(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action"}, {"item"}, "unexpected_intent_field")
    result = {"action": "eat"}
    if raw.get("item") is not None:
        result["item"] = _identifier(raw.get("item"), "invalid_item")
    return result


def _blueprint(raw: Mapping[str, Any]) -> dict[str, Any]:
    if any(key in raw for key in _ABSOLUTE_COORDINATES):
        raise MinecraftContractError("forbidden_absolute_coordinate")
    _exact_fields(raw, {"action", "anchor", "blocks"}, {"dimension", "player"}, "unexpected_intent_field")
    anchor = str(raw.get("anchor") or "")
    if anchor not in {"bot", "player"}:
        raise MinecraftContractError("invalid_blueprint_anchor")
    player = ""
    if anchor == "player":
        player = _player(raw.get("player"))
    elif "player" in raw:
        raise MinecraftContractError("unexpected_intent_field")
    rows = raw.get("blocks")
    if not isinstance(rows, list) or not rows:
        raise MinecraftContractError("empty_blueprint")
    if len(rows) > 128:
        raise MinecraftContractError("blueprint_too_large")
    blocks: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    minimum = [10_000, 10_000, 10_000]
    maximum = [-10_000, -10_000, -10_000]
    for row in rows:
        block = _mapping(row, "invalid_blueprint_block")
        _reject_code_fields(block)
        if any(key in block for key in _ABSOLUTE_COORDINATES):
            raise MinecraftContractError("forbidden_absolute_coordinate")
        _exact_fields(block, {"offset", "block"}, set(), "unexpected_blueprint_field")
        offset_raw = block.get("offset")
        if not isinstance(offset_raw, list) or len(offset_raw) != 3:
            raise MinecraftContractError("invalid_blueprint_offset")
        offset = tuple(_integer(value, -64, 64, "invalid_blueprint_offset") for value in offset_raw)
        if offset in seen:
            raise MinecraftContractError("duplicate_blueprint_offset")
        seen.add(offset)
        name = _block_identifier(block.get("block"), "invalid_block")
        for index, value in enumerate(offset):
            minimum[index] = min(minimum[index], value)
            maximum[index] = max(maximum[index], value)
        blocks.append({"offset": list(offset), "block": name})
    axes = [maximum[index] - minimum[index] + 1 for index in range(3)]
    if any(axis > 16 for axis in axes) or axes[0] * axes[1] * axes[2] > 4096:
        raise MinecraftContractError("blueprint_bounds_exceeded")
    result: dict[str, Any] = {
        "action": "place_blueprint",
        "anchor": anchor,
        "dimension": _dimension(raw.get("dimension", "overworld")),
        "blocks": blocks,
    }
    if player:
        result["player"] = player
    return result


def _deposit(raw: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(raw, {"action", "items"}, {"container", "radius", "dimension"}, "unexpected_intent_field")
    container = str(raw.get("container", "chest"))
    if container not in {"chest", "barrel", "shulker_box"}:
        raise MinecraftContractError("invalid_container")
    rows = raw.get("items")
    if not isinstance(rows, list) or not rows or len(rows) > 16:
        raise MinecraftContractError("invalid_deposit_items")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        item = _mapping(row, "invalid_deposit_item")
        _reject_code_fields(item)
        _exact_fields(item, {"item", "count"}, set(), "unexpected_deposit_field")
        name = _identifier(item.get("item"), "invalid_item")
        if name in seen:
            raise MinecraftContractError("duplicate_deposit_item")
        seen.add(name)
        items.append({"item": name, "count": _integer(item.get("count"), 1, 64, "invalid_item_count")})
    result: dict[str, Any] = {
        "action": "deposit",
        "container": container,
        "items": items,
        "radius": _integer(raw.get("radius", 8), 1, 16, "invalid_action_radius"),
    }
    if "dimension" in raw:
        result["dimension"] = _dimension(raw.get("dimension"))
    return result


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MinecraftContractError(code)
    return value


def _exact_fields(
    value: Mapping[str, Any],
    required: set[str],
    optional: set[str],
    extra_code: str,
    *,
    missing_code: str = "missing_intent_field",
) -> None:
    fields = set(value)
    if not required.issubset(fields):
        raise MinecraftContractError(missing_code)
    if fields - required - optional:
        raise MinecraftContractError(extra_code)


def _reject_code_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in _FORBIDDEN_CODE_KEYS:
                raise MinecraftContractError("forbidden_code_field")
            _reject_code_fields(child)
    elif isinstance(value, list):
        for child in value:
            _reject_code_fields(child)


def _identifier(value: Any, code: str) -> str:
    clean = str(value or "").strip().casefold()
    if not _IDENTIFIER.fullmatch(clean):
        raise MinecraftContractError(code)
    return clean


def _block_identifier(value: Any, code: str) -> str:
    clean = _identifier(value, code)
    local_name = clean.rsplit(":", 1)[-1]
    if local_name in DANGEROUS_BLOCKS:
        raise MinecraftContractError("dangerous_block_denied")
    return local_name if clean.startswith("minecraft:") else clean


def _player(value: Any) -> str:
    clean = str(value or "").strip()
    if not _PLAYER.fullmatch(clean):
        raise MinecraftContractError("invalid_player")
    return clean


def _integer(value: Any, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MinecraftContractError(code)
    return value


def _boolean(value: Any, code: str) -> bool:
    if not isinstance(value, bool):
        raise MinecraftContractError(code)
    return value


def _dimension(value: Any) -> str:
    clean = str(value or "")
    if clean not in _DIMENSIONS:
        raise MinecraftContractError("invalid_dimension")
    return clean
