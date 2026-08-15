# Minecraft 目标切片闭环方案

> 状态：方案。目标切片由用户定义，本文把切片逐段对照现状、列出缺口并给出分阶段实施方案。战斗策略已拍板**方案 A**（2026-08-15，理由见 §5）。
> 关联：`docs/AIRI_ABSORPTION_PLAN.md`（语音/记忆/生态吸收）、`docs/MINECRAFT_REALTIME_P0_P2.md`、`docs/MINECRAFT_REALTIME_P3_P6.md`、`docs/REALTIME_VOICE_DEBUG.md`。
> 修订：2026-08-15 按 `docs/MINECRAFT_PLAN_REVIEW_2026-08-15.md` 并入 A1/A2/A3 字面修正、S1.0（B1–B3 前置工程）、B4 抢占规则与 C 组三条。

## 1. 切片定义（验收标准）

用户打开 Joi → 启动 Minecraft Skill → 配置好 → Joi 进入玩家世界 → 可开启实时语音 → 实时交流中 Joi 能通过代码（游戏状态）或观察游戏屏幕理解用户意图并执行操作 → Joi 按角色人设 harness 产生自主行为（例：设定为胆小，战斗开始时不主动攻击，等用户明确指令后才攻击）→ 行为越生动越好，会主动聊天并询问用户。

验收：一条真实 HMCL/LAN 世界走查（进服 → 语音对话 → 屏幕/状态理解意图 → 执行 → 战斗场景人格反应 → 主动搭话），加全部既有门禁测试通过。

## 2. 现状对照（逐段）

| 切片环节 | 状态 | 证据/锚点 |
|---|---|---|
| 打开 Joi | ✅ | debug 包存在（Web 层过期见风险） |
| 启动 Skill、配置 | ✅ | App.vue Minecraft Skill 面板、连接配置、digest 审批 |
| 进入玩家世界 | ✅（已实测） | mineflayer 4.37.1 + 26.1 shim，真实进服通过 |
| 开启实时语音 | ✅（时延未实测） | Qwen Realtime WSS + 本地 GPT-SoVITS |
| 代码/状态理解意图 | 🟡 部分 | `observe/inventory` 走 mineflayer 状态；回执只有 5 个键（dimension/health/food/inventory_slots/inventory_total），无实体/时间/天气；且 `_safe_observation` 是精确集合匹配，扩充必须三处白名单同步 |
| 观察屏幕理解意图 | ❌ 未接入 | 截图+OCR+视觉管线只在 watch/computer-use（`core/tools/screen_observe.py`、`core/watch.py`），Minecraft 会话无此通道 |
| 执行操作 | 🟡 10 原语无战斗 | `minecraft_contract.py::GAME_ACTIONS` 无 attack/flee/guard；bridge 无任何 mob/attack 代码 |
| 人设驱动自主行为 | ❌ | `realtime_voice.py::_minecraft_instructions` 只收语言参数，`CharacterHarness.persona/tone/boundaries` 未注入；无 Minecraft 自主循环 |
| 主动聊天/询问 | ❌ | 已有可复用件：`watch_loop.py` proactive（台词/emotion/sprite 通道）、`subconscious.py`（空闲主动观察），但都不知游戏状态 |

## 3. 缺口清单

- G1 屏幕观察未接入 Minecraft Skill（管线现成，缺接线）。
- G2 战斗原语与战斗感知完全不存在（契约、服务、桥接三处都要加）。
- G3 人设未注入 Minecraft 实时语音与自主循环。
- G4 无游戏态驱动的自主行为循环。
- G5 无主动聊天/询问（可复用 watch_loop proactive 通道）。
- G6 承接上一轮：realtime 时延未实测、已装 debug App 的 Web 层早于源码、句子级流式 TTS 未做。
- G7 桥接地基缺失（评审 B1–B4）：bridge 无主动推事件通道、Core 无 `state.snapshot` 调用方、`_events`/`_seen_output` 无驱逐、单 goal 阻塞提交与 ticker 互撞。→ 并入 S1.0（B1–B3）与 S3.1 抢占规则（B4）。

## 4. 分阶段实施

### S0 基线（已有，不重做）

配置/审批/进服/实时语音/10 原语/回执/no-replay/owner-bound 全部保留，作为后续所有阶段的底座。

### S1.0 桥接地基（评审 B1–B3；先做、单独验收，S1.3 与 S3 都依赖它）

- **B1 主动事件通道**：`MinecraftBridgeClient` 现在的事件只能靠 `_wait_for(predicate)` 一问一答取出（`minecraft_bridge.py:452` 是唯一出口），`combat.started/ended` 这类无人询问也要送达的事件会进 `self._events` 后无人认领。需要：客户端订阅回调（`on_event` 注册）→ `_read_stdout` 收到非 reply 事件时泵送给订阅者 → `MinecraftGameService` 转发到 collaboration/事件总线 → shell。
- **B2 `state.snapshot` 调用方**：bridge 已实现 `state.snapshot.request`（`index.js:918`），但 Core 侧没有任何代码发过这条请求。给 `MinecraftBridgeClient` 加 `request_snapshot()`（仿 `_control` 的 send+wait），供状态页与 S3 ticker 按需调用。
- **B3 缓冲驱逐与上限**：`_events`/`_seen_output` 目前无上限，ticker + 战斗事件流会让长会话持续泄漏，`_wait_for` 还要线性扫描。改法：消费即驱逐 + maxlen 上限，`_wait_for` 只扫最近窗口。
- 验收：fake 桥接注入主动事件 → Core 收到并转发；长会话压力测试无泄漏；`request_snapshot()` 返回 sanitized snapshot。

### S1 感知与人设地基（先做，全部后续阶段依赖它）

- **S1.1 人设注入**：`RealtimeVoiceCoordinator` 增加 `persona` 回调（仿 `voice_locale/chat_locale` 的按会话读取模式，该模式已在 2026-08-15 语言改造中落地，照抄即可），`_minecraft_instructions` 拼入 `CharacterHarness` 的 persona/tone/boundaries 摘要与安全准则（不攻击玩家、坐标不出语音、不承诺未确认结果）。人设摘要构造器用 `character.py::prompt_header()`（注意：字段是 `boundaries`，不是 `rules`；函数名不是 `role_prompt`）。人设属整场不变内容，放 instructions；按轮变化的上下文不能放这里（见 `AIRI_ABSORPTION_PLAN.md` §M2 的 A1 修正）。
- **S1.2 屏幕观察通道**：Minecraft 会话激活时，`observe` 之外新增 `observe_screen`（或 observe 的 `evidence: "screen"` 变体）：复用 `tools/screen_observe.py` 的截图+OCR+视觉摘要，结果按既有隐私投影 sanitized 后作为**文本摘要**注入 provider 上下文（云端只收摘要文本，原图/OCR 明细不进云端、不落盘）；麦克风披露文案同步补“屏幕观察”一项。macOS 屏幕录制权限沿用 watch/computer-use 既有路径。
  - **不进语音回合同步路径**（评审 C2）：截图+OCR+视觉调用是秒级的，塞进实时语音一轮会毁掉延迟。改为按节奏异步采集、缓存最近一份 sanitized 摘要，provider 回合只注入缓存文本。
  - **未配视觉模型的分支**：当前配置 `llm.vision_enabled: false`，此时 S1.2 退化为纯 OCR 摘要（画面文字线索），方案与披露文案都要写明这一降级，不能假装有视觉理解。
- **S1.3 桥接战斗感知**：`minecraft-bridge/index.js` 监听 `entitySpawn/entityHurt/physicsTick`，`state.snapshot` 增加 `nearby_hostiles`（类型+数量，无坐标）；新增 sanitized 事件 `combat.started/combat.ended`（bot 被敌对生物攻击/脱离）。协议输出 schema 相应扩展（Python 端 `_validate_event_payload` 同步）。**依赖 S1.0**：combat 事件是“无人询问也要送达”的推送事件，没有 B1 通道就到不了 Core/UI。

### S2 战斗人格策略

- **S2.1 新原语**：契约层加 `attack`（目标=半径内最近敌对生物）、`flee`（远离敌对生物）、`guard`（原地警戒，无改块）。全走既有 canonicalize/gate/预算/回执；**PvP 永禁须双端成立**（评审 C3）：契约层看不到实体类型，“半径内最近的敌对生物”是 bridge 解析的，所以除契约层拒绝 player 目标外，bridge 的 `bot.nearestEntity` 过滤条件必须同样只认 hostile mob，两层都拦。
- **S2.2 人格策略层（Core 裁决，不交给模型自由裁量）**：harness 增加可选 `minecraft_policy` 字段（角色包可配，缺省用安全默认）。策略表示例：

| 人设 trait | 战斗开始默认 | 明确指令后 |
|---|---|---|
| 胆小/timid | 不提案攻击；自动提案 `flee` + 惊慌台词 + 询问用户“要打吗？” | 允许 `attack`（仍过范围/预算门） |
| 勇敢/brave | 警戒台词 + 提案 `guard`，v1 攻击仍需用户确认（见 §5 选项） | 允许 `attack` |

- 策略表由 Core 解析成结构化规则注入 provider instructions，模型只能按规则提案。

### S3 自主行为与主动聊天

- **S3.1 自主 ticker**：Minecraft 会话激活时，Core 每 20–60s（可配置，带上限）用「人设摘要 + 最近 snapshot + 屏幕摘要 + 世界记忆」组装一次轻量 LLM 调用，输出三选一：`{说话}`、`{行为提案}`、`{无}`。说话走 `safe_voice_line` + GPT-SoVITS + 字幕 + expression 表情；行为提案走 S2 全部门禁。用户语音插话时暂停 ticker（epoch 门禁复用）。
  - **与用户指令互撞规则（评审 B4）**：一个会话只有一个 goal 且 `submit_goal` 是阻塞调用（默认动作超时 120s），ticker 与语音指令必然互撞。规则：**用户指令抢占 ticker**；ticker 目标被抢占时只跳过不排队；ticker 跑独立线程、随会话停止可取消；ticker 提交前检查 `active_goal` 为空。
  - **自主预算归属（评审 C1）**：`max_actions`/`max_blocks_changed` 是用户为本次会话批准的额度，ticker 静默消耗会让“我还没下指令预算就没了”。v1 默认：自主行为与用户共享同一额度，并在 scope 审批文案里写明；「自主行为单独子预算」留作后续配置项。
- **S3.2 主动询问**：ticker 允许问题型台词（“那边有怪，要我去看看吗？”“背包快满了，要造个箱子吗？”），复用 `watch_loop.py` 的 proactive_reply/proactive_voice_text/emotion/sprite 通道模式。
- **S3.3 生动性**：新增无改块小动作提案 `wander`（scope 半径内随机路径，pathfinder 已有能力）、`look_at`；台词与表情/口型联动（expression 通道已有）。

### S4 闭环与打磨（承接 `AIRI_ABSORPTION_PLAN.md`）

- V1 句子级流式 TTS、V2 时延实测、V3 AudioWorklet；
- M1 实时会话落库、M2 世界级游戏记忆（含“胆小”等策略偏好记忆）；
- 重建 debug App（Web 层过期问题）、真实服走查全部原语 + 战斗场景。

## 5. 战斗策略默认值（已拍板 2026-08-15：方案 A）

- **方案 A**：任何人格下，`attack` 一律需用户明确指令；自主循环最多提案 `flee/guard` 与台词。
- 拍板理由不是保守，是**一致性**：Joi 现在每一个改变世界的动作都有逐动作门禁 + 回执 + no-replay。`attack` 是唯一会主动伤害实体的原语，让它在自主循环里免审批，等于在体系里最危险的动作上开唯一的例外。方案 A 已足以满足切片验收里「胆小人设 = 不主动打、等指令」的演示，且不需要为每个人格建审批矩阵。
- **方案 B**（按人设分级，勇敢类可自主提案攻击）不在 v1 做；等 A 在真实服跑通、有数据后单独立项。

## 6. 门禁与测试

- 每阶段沿用：`unittest discover`、`test:shell`、`vue-tsc`、`npm run build`、`node --check index.js`、`minecraft_p5_smoke.py`；新增：S1 屏幕摘要隐私测试（原图不进云端/不落盘）、S2 攻击对象矩阵测试（敌怪允许/玩家拒绝/范围外拒绝/预算扣减）、S3 ticker 频率上限与插话暂停测试。
- 真实服 smoke 清单：进服 → 语音对话 → observe_screen 理解意图 → 执行 → 触发 combat.started 验证人格反应（胆小=逃跑+询问，收到指令后攻击）→ ticker 主动搭话。
- 非目标：PvP、自由代码执行、自动重放失败动作、屏幕原图上传云端。
- 代码评审重点检查项（评审 H）：协议三处白名单（bridge 输出 / `_safe_observation` / `_validate_event_payload`）是否同步扩展；无人认领事件的驱逐与缓冲上限；ticker 与用户目标的抢占语义及 ticker 线程可取消性；`attack` 在 bridge 侧的 PvP 拦截（不能只在契约层）；自主动作的预算归属与审批文案一致性；屏幕摘要原图不进云端、不落盘。

## 7. 风险

- 攻击能力是信任面扩大，必须敌怪-only + 半径/预算/频次三重约束 + 用户明确指令（方案 A 下）。
- 屏幕摘要进云端增加披露面，保持“只摘要、不原图、不落盘”。
- 自主 ticker 增加 LLM 调用成本与打扰风险，频率上限 + 静音开关必须落地。
- 战斗人格策略不能由模型自行发挥，Core 解析结构规则后注入。

## 8. 实施进度（分支 minecraft-slice-dev / worktree Joi-minecraft-slice）

- **S1.0 完成**（2026-08-15）：`MinecraftBridgeClient.add_event_listener`（reply_to=="" 的主动事件泵送，监听器在 stdout 读线程上执行、带 disposer）；`request_snapshot()`；`_events`/`_seen_output` 消费驱逐与 512 上限；JS 侧 `seenMessages`/`cachedResponses` 4096 上限；fake 桥接新增 `JOI_MINECRAFT_FAKE_PUSH_SNAPSHOT_MS` 主动快照推送；`GameAdapterRegistry` 订阅事件到 per-session deque(64) 并提供 `minecraft_snapshot`/`minecraft_session_events`。验收测试 3 个。
- **S1.1 完成**：`RealtimeVoiceCoordinator` 增加 `persona` 按会话读取回调；`_conversation_instructions`/`_minecraft_instructions` 注入 sanitized+1,200 字符上限的人设块（会话级 instructions 不变内容，按轮上下文仍走 `conversation.item.create`）；server 以 `_realtime_persona_prompt`（`CharacterHarness.prompt_header()`）接线。验收测试 4 个。
- **S1.3 完成**：bridge 战斗感知——`nearby_hostiles`（类型+数量、无坐标，作为 observation 的可选键，三处白名单同步扩展）、`combat.started/combat.ended` 主动事件（真实服：被攻击启动、8 格内无敌怪清除；fake 用 `JOI_MINECRAFT_FAKE_COMBAT_MS` 交替触发）。验收测试 2 个。
- 验证：`test_minecraft_v2` 34 通过、`test_realtime_voice` 27 通过、`test_realtime_privacy_contract` 3 通过；`minecraft_p5_smoke` 通过；`node --check` 通过；ncc 包已重建。
- **待做**：S1.2（observe_screen 契约 + Core 异步屏幕摘要缓存）、S2（attack/flee/guard + 方案 A 人格策略）、S3（自主 ticker + 主动聊天）、S4。
