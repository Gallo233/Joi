# Joi Roadmap

Joi 的目标是一个角色人格包裹的多模态 Agent 伴侣：能陪看网页/视频/游戏画面，能执行游戏技能，也能通过 Codex/MCP/本地工具推进工程任务。

**这份文档记录能力轨道（P0–P10）的建设过程，不定义发布范围。** 发布范围以 `docs/JOI_PRD.md` 为准，交付阶段以 `docs/JOI_TDD.md` 的 Phase 0–5 为准；两者冲突时以 PRD/TDD 为准。注意两套编号是不同的轴：这里的 P5 是「记忆内核」这条能力轨道，TDD 的 Phase 5 是「发布候选」这个交付阶段。

OpenHuman-inspired direction is recorded in `docs/OPENHUMAN_INSIGHTS.md`. The key update is that Joi should become an embodied personal agent companion, not a generic integration dashboard: after P4, prioritize local memory, tool-result compression, model routing, skill manifests, and a constrained background companion loop.

## 当前位置

交付阶段：**TDD Phase 4（扩展与供应链）→ Phase 5（发布候选）之间**。Phase 5 的退出条件是所有 P0 追踪项有证据、Safety 与 Quality 独立门禁通过；TDD §22 的 closeout review 结论是当前仍缺 required macOS CI 证据、签名公证、干净机、权限、多屏、VoiceOver 与升级回滚证据，**尚不能称 release-ready**。

平台方向：**macOS-first**（PRD §6.1、TDD ADR-001）。Windows 保留为共享契约的兼容路径，早期文档里的 "Windows-first" 表述已经过期。

### 1.0 发布主线 vs Beta 轨道

PRD §5.2 冻结了这条分界，它决定什么能阻塞发布：

- **1.0 主线（Hero Journey）**：安装 → 基础配置 → 与默认角色的一次真实对话 → 进入项目 → 观察一个可信窗口 → Joi 提出非敏感动作 → 逐步确认 → 执行并重新观察验证 → 生成待确认记忆 → 用户确认。
- **Beta 轨道（不得阻塞首发，独立验收）**：Watch/Scene、第三方 Skill 安装、**游戏适配器**、完整角色 CRUD、Coding Agent takeover。

推进 1.0 需要的是 Phase 5 的收口证据，不是继续扩展 Beta 轨道的能力。

## P0 Project Discipline

Status: in progress

- Keep roadmap, changelog, known issues, and feedback log in `docs/`.
- Every user experience report should be attached to one product branch.
- Every fix should leave a short note and, after commit, a commit id.

## P1 Product Shell

Status: in progress

- Hide raw tool/event names from the default UI.
- Show normal chat as dialogue only.
- Show tool work as task cards with concise status.
- Add a developer mode for raw event inspection.
- Add one-click Windows launch.
- Replace placeholder stage with character assets. Local configured sprites are supported; bundled original expression/state variants are next.

## P2 Vision Layer

Status: in progress

- Add screenshot capture for browser, active window, and full screen. Windows active-window/fullscreen capture is connected through `observe.screen`.
- Add `VisionSummarizer` with configurable vision model. OpenAI-compatible vision summaries are connected.
- Add OCR grounding for visible text and rough screen regions. Optional `pytesseract`/Pillow OCR is best-effort; unavailable or timed-out OCR does not fail screenshots or Watch Together.
- Group OCR text into coarse regions and expose semantic target candidates for phrases such as "登录按钮" or "右上角".
- Add optional Windows accessibility-tree snapshots for active-window UI controls, including name, role, bounds, enabled, and clickable state.
- Attach active-window capture rectangles and preview boxes so semantic targets can be reviewed visually before approval.
- Rank semantic target candidates with explainable confidence and hold ambiguous matches for user clarification.
- Continue pending semantic target selection from user phrases such as "选 2" or from candidate-card buttons, while preserving click approval.
- Bind candidate-card selection to explicit session-only `selection_id` values so old cards cannot accidentally reuse the newest pending target context.
- Show semantic target evidence cards with source, confidence band, ambiguity/actionability gates, capture trust, and a plain confirmation reason.
- Separate text model, vision model, and expression model. Model routing is connected for text, vision, and expression.
- Do not mark blank pages or failed captures as success.

## P3 Computer Use Adapter

Status: in progress

- Add `agent_companion.core.computer_use` with observation, action, backend, and result schemas.
- Route `observe.screen` through the computer-use observation chain.
- Add `computer.click`, `computer.type_text`, `computer.scroll`, and `computer.hotkey` adapters.
- Keep all computer actions at medium risk by default and require user confirmation.
- Bind confirmations to one-time `approval_id`, task id, step index, tool, and arguments hash.
- Observe the active window after each successful computer action and attach the after screenshot to the task card.
- Compare before/after observations for confirmed actions and label the task card as changed, likely no-op, or unavailable without overclaiming success.
- Run configured OCR on Computer Use before/after observations and wait briefly after actions so real Windows UI updates can settle before verification.
- Resolve semantic click requests into candidate OCR regions first, then ask for approval before executing the synthesized click.
- Fuse accessibility-tree controls with OCR boxes for semantic target grounding, preferring real UI names/roles when available and falling back to OCR when unavailable.
- Convert approved semantic target centers from screenshot-relative OCR bbox to Windows screen coordinates only when capture rect and scale are available.
- Continue ambiguous semantic target selections from the saved session context and require a fresh approval before clicking.
- Candidate-card selection now uses explicit `selection_id`/rank RPC; text and voice phrases such as "选 2" remain a latest-context fallback.
- Candidate and approval cards explain why confirmation is needed while keeping bbox, ids, paths, logs, and commands out of spoken lines.
- Use clipboard paste for reliable Windows text input, including Chinese.
- Show friendly action summaries in task cards while keeping coordinates, text payloads, command-like details, and raw ids out of voice lines.

## P4 Watch Together Loop

Status: done

- Watch together: observe visible content, summarize, and discuss. Screen capture and optional visual model summarization are connected.
- Keep recent watch context in the session: user question, window title, summary, screenshot artifact, and model status.
- Keep recent OCR snippets in the watch session so follow-up questions can cite visible text, titles, labels, and button-like strings.
- Route follow-up questions like "what did you see" through recent visual context instead of repeating screenshots.
- Generate watch follow-up answers through the role-aware text/expression model when available, with template fallback when not configured.
- Keep screen observations out of long-term memory by default; only session watch context is retained unless the user explicitly asks to save it.
- Show screenshot artifacts as clickable task-card thumbnails instead of plain labels.
- If the vision model is unavailable, save the screenshot and clearly tell the user that vision configuration is needed for summaries.
- Game: OK-WW dry-run, approval, launch, status callback.
- Coding: Codex approval, execution, task card, result summary.
- Closeout harness: `docs/P4_CLOSEOUT_EXPERIENCE.md` defines four real experience scripts for browser buttons, Watch Together, canvas/video controls, and game/HUD. Local results go to ignored `data/local_visual_eval/p4_closeout_report.local.md`.

Remaining P4 closeout risks:

- UIA quality depends on the target program.
- Canvas/game target discovery still uses lightweight heuristics.
- Overlay/capture trust needs real multi-monitor and display-scaling validation.

## P5 Memory Core

Status: done

- Add local SQLite storage for user preferences, project summaries, game habits, recent task outcomes, and companion relationship notes.
- Add human-readable Markdown summaries for durable handoff, similar to a local memory vault.
- Keep screen observations, raw OCR, screenshots, logs, local paths, and secrets out of long-term memory by default.
- Add explicit UI actions for remember, forget, delete, and disable memory.
- Emit `memory_candidate` records from Codex, browser/watch, game skill, and normal chat flows, but save only after policy allows it.

## P6 JoiJuice Tool Compression

Status: done

- Centralize tool-result splitting into `agent_state`, `display_card`, `voice_line`, `memory_candidate`, and `audit_log`.
- Compress large tool outputs before they reach planner/model context.
- Keep `voice_line` free of JSON, ids, raw commands, paths, tokens, logs, coordinates, screenshots, provider names, and inflated results.
- Add tests for Codex logs, browser/OCR output, Computer Use events, ASR/TTS errors, and game-skill results.

## P7 Model Router

Status: in progress

- Route chat, coding, vision, and expression to different models.
- Record provider, model, latency, and fallback reason.
- Show current model usage in settings.
- Keep stable route labels: `fast`, `reasoning`, `vision`, `code`, `summarize`, and `voice_style`.
- Core router now accepts `llm.routes` overrides, preserves `text`/`expression` aliases, and reports only safe model usage metadata.

## P8 Voice, Expression, And Skill Manifest

Status: done

- Add click-to-record voice input. Shell recording, explicit ASR readiness, transcript display, OpenAI-compatible ASR, payload limits, and JSON-RPC routing are connected.
- Harden voice runtime safety: oversized base64 is rejected before decode, recorded blobs are size-checked before upload, and ASR timeout/error paths produce friendly task cards.
- Keep `MockAsrProvider` for tests/developer mode only; production microphone UI is disabled when ASR is not configured.
- Serialize `user.message`, `voice.transcribe`, and `approval.resolve` mutations through a Core command lock.
- Queue voice separately from text display and suppress stale voice audio with a client-side epoch when user intent changes.
- Align voice transcription RPC timeout with configured ASR timeout so ASR failures surface as friendly Joi messages.
- Match voice audio by event identity, including timestamp, to avoid collisions from repeated task lines.
- Show sanitized ASR/TTS runtime status in developer mode.
- Avoid speaking logs, JSON, paths, commands, tool ids, and inflated results.
- Formalize native Joi skills with manifest, input schema, result schema, permission level, dry-run support, local capability checks, state policy, and tests.
- Treat Codex, Browser/Computer Use, OK-WW, Memory, ASR, and TTS as native core skills before chasing broad third-party integrations.
- Native skill manifest V1 now reports built-in skill ids, tool/RPC bindings, permission level, dry-run support, local capability, state policy, and audit policy through safe `core.ready` / `skills.list` payloads.
- Plan, approval, tool result, task lifecycle, and Computer Use audit events now carry native skill boundary metadata for permission and audit UI work.
- Native skill enable switches are now safe runtime config fields; disabled skills show as off in the manifest and are blocked by policy before approval or execution.

## P9 Policy, Audit, And Background Companion Loop

Status: in progress

- Low risk actions run directly.
- Medium risk actions require task-level confirmation.
- High risk actions require step-by-step confirmation.
- Persist audit records for tool actions and approvals.
- Persistent audit V1 now records approval, tool, task, and policy-block lifecycle rows to a local sanitized JSONL and exposes safe status plus `audit.recent`.
- Add constrained background observation only for user-approved windows, projects, and games.
- Background context controls now require an approved window/project/game scope and store summary-only context, with no video recording by default.
- Summarize approved context without recording video by default.
- Let users inspect, clear, or disable background context.
- Shell developer controls now expose background status, approved scopes, recent summaries, disable, clear, and scope approval.

## P10 Packaging

Status: in progress — macOS 是发布平台，Windows 工具链保留为兼容路径

macOS（发布路径，`docs/MACOS_RELEASE.md` 是权威流程）：

- `Joi macOS Draft Release` workflow 已就绪：`macos-15` runner、semver tag 或手动触发、签名公证、上传 draft prerelease、记录 build-provenance attestation。
- `ci.yml` 已有 required 的 `macos-15` lane 与 Windows 兼容 lane。
- Tauri 打包 fail-closed：构建独立 Core sidecar、要求完整 Live2D 源、逐文件校验 `release-assets.json` 的 pinned hash。
- `tools/packaging_smoke.py` 覆盖版本对齐、Tauri 元数据、窗口权限、启动器接线、release 隐私策略。
- 未完成：干净机验收、真机签名公证证据、多屏/VoiceOver/权限撤销/升级回滚证据。
- 已知缺口：`agent_companion/adapters/minecraft-bridge/dist/` 是 gitignored，而 sidecar 打包会原样拷贝 `adapters/`。CI 的全新 clone 里没有这个构建产物，因此 release 包中的 Minecraft 适配器会显示未就绪。要么在 workflow 里补一步 bridge 构建，要么在 release notes 里说明该包不含游戏能力。

Windows（兼容路径，不构成 1.0 承诺）：

- Portable Windows release packager now creates a safe zip from allowlisted runtime files and the release shell.
- Release privacy validation now checks that packaging rules protect local config, secrets, runtime data, logs, dependency folders, and build caches before Windows artifacts are shipped.
- Release readiness aggregation now combines doctor, packaging smoke, privacy policy, and portable package dry-run status into one safe RC report.
- First-run setup checklist.
- First-run setup wizard now exposes `start_joi.bat -Setup` and can create local `config.yaml` from the example without writing secrets.
- First-run doctor now checks Python packages, optional OCR/audio packages, frontend toolchain, shell build state, `config.yaml`, Tesseract, and Core port readiness.
- Offline provider preflight now reports sanitized readiness for text, vision, expression, ASR, TTS, OCR, Computer Use, and audit/verification without endpoint or secret probes.
- MVP demo readiness now covers safe Watch Together, Codex coding, and OK-WW game-skill scripts without launching external actions.
- Packaging smoke now validates version alignment, Tauri shell metadata, window permissions, and launcher wiring.
- Mac handoff kept current through a safe Windows RC handoff report that summarizes release readiness without local paths or secrets.
- CI workflow now runs Python tests, packaging smoke, frontend build, and Tauri debug no-bundle build on Windows.

## B1 Beta 轨道：游戏适配器与实时语音

Status: in progress — 独立验收，不阻塞 1.0

这条轨道过去没有在 roadmap 里登记，但已经有实现和测试，写在这里以免它看起来像 1.0 范围。

- Minecraft GameAdapter v2：13 个严格 GameIntent 原语（含 attack/flee/guard），逐动作权限、scope、预算、回执与 no-replay。
- 26.1 版本兼容：mineflayer 的 tested-version 门只对本桥接带有验证 shim 的版本放行，更新的版本仍 fail closed。
- 桥接推送通道：combat/chat/snapshot 等非应答事件能到达 Core，缓冲有上限。
- Core 侧屏幕证据：截图只在本机做摘要和 OCR，读完即删，云端只收文字。
- 自主 ticker：可开关、有频率上限、用户指令抢占；attack 在实时语音、autonomy 与 service 三层都要求用户明确指令（Scheme A）。
- 计划编译器与游戏内聊天：都编译成同一套 GameIntent，走同一套门禁。
- 实时语音：Qwen Audio Realtime 只收文本，本地 GPT-SoVITS 发声；朗读语言跟角色包，字幕语言跟聊天语言设置。
- 未完成：真实服的战斗场景走查、实时语音时延实测、句子级流式 TTS、AudioWorklet 采集。
- 参考：`docs/MINECRAFT_CLOSED_LOOP_SLICE.md`、`docs/AIRI_ABSORPTION_PLAN.md`、`docs/MINECRAFT_PLAN_REVIEW_2026-08-15.md`、`docs/REALTIME_VOICE_DEBUG.md`。

## 发布阻塞项

工程之外的决策，未完成前 GitHub release 只能停留在 draft/prerelease（`docs/MACOS_RELEASE.md`）。

| 阻塞项 | 状态 |
|---|---|
| 软件许可证 | 仓库尚无 LICENSE 文件 |
| 隐私声明 | `docs/PRIVACY.md` 仍是 Draft |
| 第三方权利通知 | `docs/THIRD_PARTY_NOTICES.md` 仍是 MVP Draft，未覆盖传递依赖 |
| 角色/图标/字体/声音/Live2D 权利 | 待确认；Live2D Cubism Expandable Application 审批未完成 |
| 应用图标 | 仍是 provisional 资产 |
| Apple 签名与公证 | workflow 已就绪，需要 6 个 Apple secrets |
| 授权资产分发 | 需要 `JOI_RELEASE_ASSETS_URL` 与 `JOI_RELEASE_ASSETS_SHA256`；授权资产不得为通过 CI 而提交进仓库 |
| 干净机验收 | 未做：需要一台没有 Python/Node/Rust/仓库/配置的 Apple Silicon Mac |
