# AIRI 吸收计划：游戏内自主性、生态、语音架构与记忆

> 状态：方案级设计评审（未实施）。依据 2026-08 debug/shell-refactor 工作树代码实审。
> 关联文档：`docs/AIRI_COMPARISON.md`（已过期，仅历史参考，不作现状证据）、`docs/MINECRAFT_REALTIME_P0_P2.md`、`docs/MINECRAFT_REALTIME_P3_P6.md`、`docs/REALTIME_VOICE_DEBUG.md`、`docs/VOICE_LATENCY_INVESTIGATION_2026-08-13.md`、`docs/KNOWN_ISSUES.md`。
> 修订：2026-08-15 按 `docs/MINECRAFT_PLAN_REVIEW_2026-08-15.md` 修正 A1（instructions 是会话级）、A3（观察字段数 5 键 + 三处白名单）、A4（认知层数说法）。
> 进度（2026-08-18）：A1–A4、M1/M2 与 V2（时延埋点）已落地，现状列写的是**立项时**的基线，不是当前代码；当前能力以 `docs/ROADMAP.md` B1 与 `docs/MINECRAFT_CLOSED_LOOP_SLICE.md` §8 为准。V1 句子级流式 TTS 与 V3 AudioWorklet 仍未做。

## 0. 现状摘要（对照 AIRI）

| 维度 | AIRI（moeru-ai/airi） | Joi 最新 debug | 差距定性 |
|---|---|---|---|
| 游戏能力 | 分层认知架构（Reflex/Conscious，另有材料描述为四层），战斗、自主采集/合成/建造，游戏内聊天下指令，MineflayerViewer 网页 POV，Debug Dashboard，MCP + Query DSL | 写这份计划时：10 个严格 GameIntent 原语，无战斗/自由规划/游戏内聊天输入/POV 查看器 | 自主性弱，生态缺 |
| 语音架构 | 链式 VAD(Silero)→ASR→LLM→TTS 四跳，语音在 stage UI | 单模型 speech-to-speech（Qwen Audio Realtime，server VAD，只收文本）+ 本地 GPT-SoVITS | Joi 时延架构占优；但 TTS 起播晚、未实测 |
| 权限/安全 | 聊天即命令，无逐动作审批/预算/回执 | digest 审批、scope/预算、Core 权威回执、no-replay、owner-bound | Joi 显著更强（不可回退） |
| 记忆 | 向量语义记忆持久化 | realtime 会话临时；checkpoint 停会话即删 | 无跨会话连续性 |

吸收原则：**自主性来自“在 10 原语之上加一层可审批的计划编译器”，不是放开权限**。以下所有吸收项必须继续经过 Core 的权限、scope、预算、回执门禁；`safe_voice_line`、坐标不入 UI/语音、no-replay、owner-bound 契约一律不变。

---

## 1. 吸收 AIRI 的游戏内自主性

### A1. 计划编译器（`core/minecraft_planner.py`）

- 输入：一条自然语言目标（voice 或 text）。
- 输出：≤8 步的严格 `GameIntent` 序列，每步仍是现有 `canonicalize_game_intent` 产物，逐步走 `MinecraftGameService.submit_goal` 全套门禁（scope / action_allowed / 预算预留 / 回执 / no-replay 全不变）。
- 交互：
  1. `minecraft_plan.preview` 返回计划预览（步骤摘要 + 预估方块改动 + 预计动作数）；
  2. 复用现有“5 分钟一次性 digest 审批”模式，用户确认后开始逐步执行；
  3. 步与步之间检查 `state.snapshot` 与剩余预算；某步失败即停，**不自动改计划、不重放**（与现有 disconnect 策略一致）。
- 示例能力：AIRI 的 “Gather 10 oak logs → Craft a wooden pickaxe → Find iron ore” 可表达为 `collect(oak_log,10) → craft(wooden_pickaxe) → observe/mine(iron_ore)`。
- 红线：
  - 计划里禁止携带坐标、会话/权限 ID（复用 `_proposal_from_call` 的 forbidden 字段集）；
  - 不引入战斗原语（见 §5 暂缓项）；
  - 模型只能提议计划，Core 编译校验后用户确认才执行。

### A2. 游戏内聊天作为第二输入通道

- Bridge 增加 `bot.on('chat')` 监听，新增协议消息 `chat.observed`（消息体：白名单玩家名、清洗后的文本、无坐标）。
- Core 把 `allowed_players` 名单内的聊天视为 `source="text"` 的 GameIntent，走与语音完全相同的 canonicalize/gate 管线。
- 约束：只认 scope 内玩家（多人服防冒充）；聊天原文经 `safe_voice_line` 同等清洗后才可进 UI/记忆；聊天触发的动作全部落审计。

### A3. 生态吸收

| AIRI 资产 | Joi 吸收方式 | 代价 |
|---|---|---|
| MineflayerViewer（网页 POV） | 复用 prismarine-viewer，仅 dev 模式、绑定 127.0.0.1 的附加端口 | 低；不进 release 包 |
| Debug Dashboard | 沿用 dev 模式运行状态页，补 Bridge 实时 sanitized 状态流 | 低 |
| MCP 集成 | 把 10 原语暴露为 MCP server（基于现有 `mcp_adapter.py`），外部 MCP 客户端调用同样过 Core 门禁 | 中 |
| Query DSL / 认知栈细节 | **不吸收**（与安全模型冲突） | — |

### A4. 观测质量提升（性价比最高）

- 现状：`observe` 回执只有 dimension/health/food/inventory_slots/inventory_total 五个键，模型“意识”很弱。
- 扩充为 sanitized 结构：附近实体类型与数量、时间/天气、可见关键方块类型计数。**仍不含坐标**，符合既有隐私契约。
- 注意：`minecraft_bridge.py::_safe_observation` 是**精确集合匹配**（`set(value) != {...}`），不是子集判断。扩充观察字段必须同步改三处白名单——bridge 输出、`_safe_observation`、`_validate_event_payload` schema——漏改任一**不会报错，而是静默返回空观察**。

---

## 2. 语音架构优化（按收益排序）

### V1. 句子级流式 TTS（最大单点延迟）

- 现状：`realtime_voice.py::_complete_response` 只在 `response.done` 拿到全文后才发一次 `assistant_text`，TTS 等整段文本生成完才开始。
- 改法：非拆分（单语言）模式下，`response.text.delta` 按句末标点切分，每凑齐一句立即送 GPT-SoVITS 流式合成（`gpt_sovits.stream_pcm16` 已支持 streaming_mode + 0.15s fragment）。
- 保留 epoch 门禁：新 delta 属于旧 epoch 即丢弃（`server.py::_synthesize_realtime_text` 的 epoch 检查已具备）。
- 效果：首音频从“全句 TTFT”降到“首句 TTFT”。

### V2. 先把时延测出来再调

- 给 realtime 链路加 dev-only 分段计时（mic 帧 → provider → response.done → 首个 TTS chunk），沿用 `transcribe_audio` 的 `provider_ms` 消毒模式（无 ID/坐标/原文）。
- 目标沿用 `VOICE_LATENCY_INVESTIGATION_2026-08-13.md`：记录 speech-stopped → assistant audio-started 的 P50/P95，再决定下一轮调优。

### V3. 采集层换 AudioWorklet

- `realtimeVoice.ts` 的 `createScriptProcessor` 已废弃且跑主线程，Tauri WKWebView 上抖动明显。
- 换 `AudioWorklet`（macOS Safari 14.1+ 支持，AIRI 同为 worklet 路线）；顺带把 40ms 帧提到 100ms（Qwen 上限 100ms），RPC 次数降约 60%。

### V4. Provider 可插拔

- `realtime_voice.py` 的事件集（session.update / response.* / function_call）即 OpenAI Realtime 形状，抽象 provider adapter 成本低；收益是时延/价格可按需切换、测试注入更简单。

### V5. 不做但要说清

- **不回退到 Qwen 音频当 Joi 声音**（现有拒绝逻辑正确，保持）。
- **游戏内 SVC 空间语音**（mineflayer-simplevoice 路线）会打破“GPT-SoVITS 是唯一声音”契约；若要支持，必须作为显式、独立审批、默认关闭的插件过 Trust & Safety gate，不塞进实时语音链路。

---

## 3. 记忆优化

### M1. 实时语音会话落库（改“临时”为“显式留存”）

- 现状：realtime 气泡本地生成、不写 Core 历史；断线即忘。
- 改法：会话结束后把清洗后的文本转写对（用户句 + Joi 句，经 `safe_voice_line`、去坐标/ID/库存明细）按 epoch 顺序写入历史，标记 `source=voice.realtime`；原始音频仍绝不落盘。
- 是否留存放进启动披露文案，让用户选择。直接影响“陪玩连续性”——现在每次重连都是陌生人。

### M2. 世界级游戏记忆（跨会话，私有一分为二）

- **Bot 私有记忆**（只 Core 可见、永不进 UI/语音/云端 instructions 原文）：上次断线位置区域、已建结构（名称+数量，无公开坐标）、最近 N 个 goal 结果。复用 `data/private/minecraft-checkpoints` 模式；`stop_minecraft_session` 目前会删 checkpoint，改为显式“清除存档”操作。
- **可注入上下文**：世界名、维度、血量/饥饿、背包要点、最近成就等 sanitized 摘要按轮注入，减少模型反复 `observe/inventory`。注意：**instructions 是会话级的**——`session.update` 只在 `start()` 发一次（`realtime_voice.py:194`），每轮上下文只能走 `conversation.item.create`（该通道已用于回传工具结果）。只有「人设、安全准则、语言规则」这类整场不变的内容才留在 instructions。进云端的任何文本必须保持 sanitized（现有隐私契约）。
- **用户偏好规则**：跨会话记住“别动我的基地”“只用橡木”，作为规则注入。这是 AIRI 没有、但陪玩场景最值钱的记忆。

### M3. 向量语义记忆：现在不引入

- AIRI 用 pgvector；Joi 已有 FTS5。对游戏陪玩，**结构化事实 + 规则**比语义向量召回有效，且 `AIRI_COMPARISON.md` Phase B2 结论也是先不上重依赖。等 M1/M2 落地后有真实召回需求再评估。

---

## 4. 路线图与门禁

| 阶段 | 内容 | 门禁 |
|---|---|---|
| P1 语音（1 轮） | ~~V2 时延计时~~（已落地）+ V1 句子级流式 TTS + V3 worklet | Quality & Release：真实麦克风 P50/P95 数据 |
| P2 自主性 | A4 观测扩充 + A1 计划编译器 + A2 游戏内聊天通道 | Trust & Safety scheme gate（聊天伪造、计划越界、战斗排除声明）；扩展 `tests/test_minecraft_v2.py` |
| P3 生态+记忆 | A3 dev 查看器/MCP + M1 会话落库 + M2 世界记忆 | 隐私 gate：落库内容清单与“坐标/音频不落盘”证明 |
| 暂缓 | 战斗原语、SVC 游戏内语音、向量记忆 | 单独立项，各自 gate |

每阶段验收沿用现有命令：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
cd agent_companion/shell && npm run test:shell && npx vue-tsc --noEmit && npm run build
node --check agent_companion/adapters/minecraft-bridge/index.js
.venv/bin/python tools/minecraft_p5_smoke.py
```

---

## 5. 风险与非目标

- **不回退安全底线**：权限、审批、预算、回执、no-replay、owner-bound、隐私投影全部保持不变；自主性只能通过“可审批组合”获得。
- **战斗/危险操作不进入计划编译器 v1**：需要 Trust & Safety 单独 gate，且 `DANGEROUS_BLOCKS`/风险分级体系要先扩展。
- **时延结论依赖实测**：所有语音调优在 V2 数据出来前不承诺具体收益。
- **非目标**：不复制 AIRI 的分层认知栈实现与 Query DSL；不引入向量数据库；不把游戏内 SVC 语音并入实时语音主链路。
- **桥接地基先行**：事件推送通道、`state.snapshot` 调用方、缓冲驱逐是切片开工的前置工程，见 `docs/MINECRAFT_CLOSED_LOOP_SLICE.md` §S1.0，必须单独验收后再进语音/自主性阶段。
