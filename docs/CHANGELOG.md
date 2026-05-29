# Joi Changelog

## v0.2.0 — Hermes Merge (win-desktop-fixes + Hermes enhancements)

### Architecture
- **Plugin System**: ToolPlugin protocol with auto-discovery from `agent_companion/plugins/`
- **MCP Adapter**: McpToolAdapter wraps external MCP servers as Joi tools (stdio + HTTP)
- **Workflow Engine**: WorkflowBuilder with conditional branching, pause/resume, retry
- **Skill Manifest**: 16 native skills formalized with permission levels and schemas
- **Tool Compression (P6)**: 5-channel split (agent_state/display_card/voice_line/memory_candidate/audit_log)

### Memory (P5)
- FTS5 full-text search for semantic recall
- Memory candidate system (propose → approve/reject)
- Obsidian-compatible vault with auto-rewrite
- Relationship memory (preference/fact/relationship)
- `browse_vault()` for frontend vault browsing
- `search_by_kind()` for typed memory retrieval

### Computer Use (P3)
- macOS AXUIElement accessibility observer (ctypes + AppleScript fallback)
- Double-click action (`computer.double_click`)
- Drag action (`computer.drag`)
- App launching with Spotlight + `open -a` fallback (`computer.open_app`)
- Retina display coordinate mapping fix
- All actions registered in planner and tool registry

### Voice & Expression (P8)
- Skill Manifest with 16 formalized native skills
- Each skill: permission_level, risk_level, input_schema, capability_check
- Skills exposed in settings panel and ready payload

### Background Loop (P9)
- SubconsciousLoop: background heartbeat every 2 minutes
- Auto-approves low-risk memory candidates
- Refreshes vault when memories change
- Generates proactive observations when idle
- RPC: subconscious.start / stop / status

### Frontend
- Mascot mood state machine: idle/thinking/listening/talking/surprised/dreaming
- Subconscious indicator: purple breathing glow + "正在整理记忆..." bubble
- Integrated settings panel: left nav + right content (7 sections)
- Memory cabin with semantic search + vault browser
- Task progress bar (workflow step visualization)
- Model route badge in topbar
- Skill manifest display in settings

### Win Branch Integration
- LlmPlanParser (LLM-driven planning)
- DesktopWorkflowTool (multi-step computer use)
- WatchLoopController (background watch-together loop)
- WatchCommentaryPlanner (proactive commentary during watch)
- SystemAudioTranscriptProvider (system audio transcription)
- Enhanced memory with candidate approval/rejection

## v0.1.0 — Initial macOS Adaptation

- Platform Factory pattern for OS-specific backends
- MacComputerUseBackend with CoreGraphics mouse/keyboard
- MacScreenObserver with screencapture + Retina crop
- Thread-safe clipboard via pbcopy/pbpaste
- Spotlight app launcher
- Glassmorphism frontend redesign
- Compact mascot mode with drag support
