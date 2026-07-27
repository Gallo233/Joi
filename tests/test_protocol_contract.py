"""Phase 0 golden contracts for the Core↔Shell boundary.

These tests freeze the surface the Vue shell parses. They are deliberately
literal: a change here should require editing the golden value, which is the
moment to also update `agent_companion/shell/src/protocol.ts` and any stored
event rows. See docs/JOI_TDD.md §6 and §17 Phase 0.
"""

from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from agent_companion.core.collaboration_store import COLLABORATION_SCHEMA_VERSION, CollaborationStore
from agent_companion.core.event_bus import PUBLIC_PHASES
from agent_companion.core.rpc.protocol import JsonRpcProtocolError, encode_error, encode_result, parse_request
from agent_companion.core.run_store import CHALLENGE_STATES, RUN_SCHEMA_VERSION, RUN_STATES, RunContext
from agent_companion.core.schemas import AgentEvent, DisplayCard, EventType, RiskLevel, ToolResult, VoiceLine


SHELL_PROTOCOL = Path(__file__).resolve().parents[1] / "agent_companion" / "shell" / "src" / "protocol.ts"


def _ts_union(source: str, name: str) -> set[str]:
    """Read a `export type X = 'a' | 'b'` union out of protocol.ts.

    Handles both the single-line form and the multi-line form whose first
    member carries a leading pipe.
    """
    match = re.search(rf"export type {name} =\s*(?P<body>\|?\s*'[^']+'(?:\s*\|\s*'[^']+')*)", source)
    if not match:
        raise AssertionError(f"protocol.ts no longer declares a {name} union")
    return set(re.findall(r"'([^']+)'", match.group("body")))


def _ts_interface_fields(source: str, name: str) -> set[str]:
    match = re.search(rf"export interface {name} \{{(?P<body>.*?)\n\}}", source, re.DOTALL)
    if not match:
        raise AssertionError(f"protocol.ts no longer declares interface {name}")
    return set(re.findall(r"^\s{2}([a-z_][a-z0-9_]*)\??:", match.group("body"), re.MULTILINE))


class JsonRpcContractTests(unittest.TestCase):
    def test_request_envelope_is_frozen(self) -> None:
        request = parse_request(json.dumps({"jsonrpc": "2.0", "id": "rpc-1", "method": " project.list ", "params": {"a": 1}}))
        self.assertEqual((request.request_id, request.method, request.params), ("rpc-1", "project.list", {"a": 1}))

        # Named-object params only; anything else normalizes to empty rather than
        # reaching a handler as a list it cannot index.
        for params in ([1, 2], "text", 7, None):
            self.assertEqual(parse_request(json.dumps({"id": 1, "method": "core.ping", "params": params})).params, {})

    def test_protocol_errors_are_frozen(self) -> None:
        for raw, code in (("{not json", -32700), (json.dumps([1]), -32600), (json.dumps({"id": 1}), -32600), (json.dumps({"id": 1, "method": "  "}), -32600)):
            with self.assertRaises(JsonRpcProtocolError) as caught:
                parse_request(raw)
            self.assertEqual(caught.exception.code, code)

    def test_response_envelopes_are_frozen(self) -> None:
        self.assertEqual(json.loads(encode_result("rpc-1", {"ok": True})), {"jsonrpc": "2.0", "id": "rpc-1", "result": {"ok": True}})
        self.assertEqual(json.loads(encode_error("rpc-1", -32601, "method not found")), {"jsonrpc": "2.0", "id": "rpc-1", "error": {"code": -32601, "message": "method not found"}})

    def test_non_ascii_is_not_escaped_on_the_wire(self) -> None:
        self.assertIn("需要确认", encode_result(1, {"summary": "需要确认"}))


class EventContractTests(unittest.TestCase):
    def test_agent_event_field_set_is_frozen(self) -> None:
        self.assertEqual(
            {field.name for field in fields(AgentEvent)},
            {
                "type",
                "task_id",
                "display_card",
                "voice_line",
                "agent_state",
                "created_at",
                "event_id",
                "sequence",
                "project_id",
                "thread_id",
                "session_id",
                "character_id",
                "public_phase",
            },
        )

    def test_public_phase_vocabulary_is_frozen(self) -> None:
        self.assertEqual(
            set(PUBLIC_PHASES),
            {"idle", "received", "understanding", "thinking", "acting", "waiting", "paused", "done", "failed"},
        )

    def test_event_serializes_type_as_a_plain_string(self) -> None:
        payload = AgentEvent(EventType.TOOL_COMPLETED, "task-1", DisplayCard("电脑操作", "完成"), VoiceLine("")).to_dict()
        self.assertEqual(payload["type"], "tool_completed")
        self.assertIsInstance(payload["type"], str)
        self.assertEqual(json.loads(json.dumps(payload))["type"], "tool_completed")


class ToolResultContractTests(unittest.TestCase):
    def test_tool_result_channels_are_frozen(self) -> None:
        self.assertEqual(
            {field.name for field in fields(ToolResult)},
            {"ok", "agent_state", "display_card", "voice_line", "requires_approval", "risk"},
        )

    def test_risk_levels_are_frozen(self) -> None:
        self.assertEqual({level.value for level in RiskLevel}, {"low", "medium", "high"})


class ShellContractTests(unittest.TestCase):
    """The Vue shell must agree with Core on every enum it switches on."""

    def setUp(self) -> None:
        self.source = SHELL_PROTOCOL.read_text(encoding="utf-8")

    def test_shell_event_type_union_matches_core(self) -> None:
        self.assertEqual(_ts_union(self.source, "EventType"), {member.value for member in EventType})

    def test_shell_public_phase_union_matches_core(self) -> None:
        self.assertEqual(_ts_union(self.source, "PublicPhase"), set(PUBLIC_PHASES))

    def test_shell_permission_profiles_match_core(self) -> None:
        from agent_companion.core.collaboration_store import PERMISSION_PROFILES

        self.assertEqual(_ts_union(self.source, "PermissionProfile"), PERMISSION_PROFILES)

    def test_shell_agent_event_carries_every_core_identity_field(self) -> None:
        declared = _ts_interface_fields(self.source, "AgentEvent")
        for name in ("project_id", "thread_id", "session_id", "character_id", "public_phase", "event_id", "sequence"):
            self.assertIn(name, declared, f"protocol.ts AgentEvent is missing {name}")


class StoreSchemaContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.store = CollaborationStore(root, data_home=root / "data", default_character_id="character-joi")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _columns(self, table: str) -> set[str]:
        return {str(row["name"]) for row in self.store._connection.execute(f"PRAGMA table_info({table})")}

    def test_schema_version_and_tables_are_frozen(self) -> None:
        self.assertEqual(COLLABORATION_SCHEMA_VERSION, 2)
        tables = {str(row[0]) for row in self.store._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual(
            {
                "meta",
                "projects",
                "conversation_threads",
                "resource_bindings",
                "events",
                "capability_sessions",
                "permission_grants",
                "action_receipts",
                "skill_installations",
                "skill_runs",
                "skill_drafts",
            },
            tables,
        )

    def test_schema_v3_run_tables_are_frozen(self) -> None:
        self.assertEqual(RUN_SCHEMA_VERSION, 3)
        tables = {str(row[0]) for row in self.store._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertLessEqual(
            {"runs", "run_steps", "checkpoints", "approval_challenges", "effect_attempts", "audit_entries", "resource_leases", "rpc_dedup", "deletion_jobs"},
            tables,
        )
        # The idempotency key is what stops a duplicate resume from acting
        # twice, so uniqueness is part of the contract, not an optimization.
        # An inline UNIQUE produces an auto-index with no SQL text, so read the
        # index metadata rather than sqlite_master.sql.
        unique_columns = {
            str(column["name"])
            for index in self.store._connection.execute("PRAGMA index_list(effect_attempts)")
            if int(index["unique"])
            for column in self.store._connection.execute(f"PRAGMA index_info({index['name']!r})")
        }
        self.assertIn("idempotency_key", unique_columns, "effect_attempts.idempotency_key must stay uniquely indexed")

    def test_run_state_vocabularies_are_frozen(self) -> None:
        self.assertEqual(set(RUN_STATES), {"created", "running", "waiting_approval", "paused", "completed", "failed", "cancelled"})
        self.assertEqual(set(CHALLENGE_STATES), {"pending", "approved", "rejected", "expired", "cancelled", "superseded", "consumed"})

    def test_run_context_identity_fields_are_frozen(self) -> None:
        self.assertEqual(
            set(RunContext(project_id="p", thread_id="t", run_id="r").payload()),
            {"schema_version", "project_id", "thread_id", "run_id", "session_id", "capability_id", "character_id"},
        )

    def test_event_and_grant_identity_columns_are_frozen(self) -> None:
        self.assertLessEqual(
            {"event_id", "sequence", "project_id", "thread_id", "session_id", "character_id", "public_phase", "task_id", "type", "created_at", "payload_json"},
            self._columns("events"),
        )
        self.assertLessEqual({"id", "session_id", "profile", "scope_json", "status", "launch_id", "created_at", "expires_at"}, self._columns("permission_grants"))

    def test_sqlite_is_configured_for_concurrent_recovery(self) -> None:
        self.assertEqual(str(self.store._connection.execute("PRAGMA journal_mode").fetchone()[0]).casefold(), "wal")
        self.assertEqual(int(self.store._connection.execute("PRAGMA foreign_keys").fetchone()[0]), 1)

    def test_reopening_an_existing_database_is_non_destructive(self) -> None:
        project_id = self.store.create_project("工作")["project"]["id"]
        data_home = self.store.data_home
        workspace = self.store.workspace
        self.store.close()

        reopened = CollaborationStore(workspace, data_home=data_home, default_character_id="character-joi")
        self.addCleanup(reopened.close)
        self.assertIn(project_id, {row["id"] for row in reopened.list_projects()})
        self.store = reopened


class LegacySchemaMigrationTests(unittest.TestCase):
    """A database written before schema v2 must still open and keep its rows."""

    def test_pre_v2_database_gains_new_columns_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_home = root / "data"
            data_home.mkdir(parents=True)
            connection = sqlite3.connect(data_home / "joi.sqlite3")
            connection.executescript(
                """
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT NOT NULL, default_character_id TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE conversation_threads(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, character_id TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE events(event_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL DEFAULT 0, project_id TEXT NOT NULL, thread_id TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', character_id TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT '', type TEXT NOT NULL, created_at REAL NOT NULL, payload_json TEXT NOT NULL);
                CREATE TABLE capability_sessions(id TEXT PRIMARY KEY, project_id TEXT NOT NULL, thread_id TEXT NOT NULL, capability TEXT NOT NULL, goal TEXT NOT NULL DEFAULT '', permission_profile TEXT NOT NULL, state TEXT NOT NULL, driver TEXT NOT NULL DEFAULT 'native', budget_json TEXT NOT NULL DEFAULT '{}', stop_conditions_json TEXT NOT NULL DEFAULT '[]', created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL);
                CREATE TABLE permission_grants(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, profile TEXT NOT NULL, scope_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, expires_at REAL);
                INSERT INTO projects VALUES('project-legacy','旧项目','character-joi',0,1.0,1.0);
                INSERT INTO conversation_threads VALUES('thread-legacy-row','project-legacy','旧对话','character-joi',0,1.0,1.0);
                """
            )
            connection.commit()
            connection.close()

            store = CollaborationStore(root, data_home=data_home, default_character_id="character-joi")
            self.addCleanup(store.close)

            columns = lambda table: {str(row["name"]) for row in store._connection.execute(f"PRAGMA table_info({table})")}
            self.assertIn("public_phase", columns("events"))
            self.assertIn("launch_id", columns("permission_grants"))
            self.assertIn("project-legacy", {row["id"] for row in store.list_projects()})


if __name__ == "__main__":
    unittest.main()
