# 方案评审：AIRI 吸收计划 + Minecraft 目标切片

> 状态：评审结论（2026-08-15）。对照 `debug/shell-refactor` 工作树实际代码逐条核对，AIRI 侧查证上游资料而非沿用表格。
> 被评审文档：`docs/AIRI_ABSORPTION_PLAN.md`、`docs/MINECRAFT_CLOSED_LOOP_SLICE.md`
> 结论：两份方向成立。吸收计划改掉 A1/A3 即可开工；切片需先补 B1/B2 两块地基。

## 0. 结论

| 文档 | 结论 | 前提 |
|---|---|---|
| `AIRI_ABSORPTION_PLAN.md` | **通过** | 改 A1（M2 机制错误）、A3（观察字段数） |
| `MINECRAFT_CLOSED_LOOP_SLICE.md` | **有条件通过** | 改 A2；把 B1–B4 补成 S1 的显式子项，其中 B1+B2 拆成 S1.0 先落地 |

---

## A. 事实错误（会直接把实现带偏，改了才能开工）

### A1. instructions 是会话级的，不是每轮的

- **位置**：`AIRI_ABSORPTION_PLAN.md` §M2
- **原文**：「每 turn 把 sanitized 摘要拼进 `_minecraft_instructions`」
- **实际**：`session.update` 只在 `start()` 里发一次，之后全程再没发过。每轮上下文只能走 `conversation.item.create`。
- **锚点**：`agent_companion/core/realtime_voice.py:194`（唯一发送点）、`:591`（`conversation.item.create` 已用于回传工具结果）
- **后果**：照原文实现，代码看着对，运行时完全不生效。
- **改法**：世界记忆/屏幕摘要按轮注入改用 `conversation.item.create`；只有「人设、安全准则、语言规则」这类整场不变的内容才留在 instructions 里。

### A2. `character.py::role_prompt` 不存在

- **位置**：`MINECRAFT_CLOSED_LOOP_SLICE.md` §S1.1
- **原文**：「`character.py::role_prompt` 已有 persona+tone+rules 格式」
- **实际**：函数名是 `prompt_header()`，字段名是 `boundaries`（不是 `rules`）。
- **锚点**：`agent_companion/core/character.py:11-25`
- **后果**：DS 会 import 一个不存在的符号。

### A3. observe 是 5 个字段，且校验是精确集合匹配

- **位置**：`AIRI_ABSORPTION_PLAN.md` §A4；`MINECRAFT_CLOSED_LOOP_SLICE.md` §2 表格
- **原文**：「只有 dimension / health / food / inventory_slots 四个数」
- **实际**：还有 `inventory_total`，共 5 个键；且校验写的是 `set(value) != {...}`，不是子集判断。
- **锚点**：`agent_companion/core/minecraft_bridge.py:551`
- **后果**：扩充观察字段时，bridge 输出、`_safe_observation` 白名单、`_validate_event_payload` schema 三处必须同步改。漏改任一处**不会报错，而是静默返回空观察**——最难定位的一类故障。

### A4. AIRI 认知层数存疑（不影响设计，勿当定论）

- **位置**：`AIRI_ABSORPTION_PLAN.md` §0 表格
- **原文**：「四层认知架构（感知→反射→意识→行动）」
- **实际**：DeepWiki 的 Minecraft agent 页描述为**两层**（Reflex + Conscious，由 `Brain` 类编排）；四层说法出自另一处材料。
- **处理**：改成「分层认知架构（Reflex/Conscious，另有材料描述为四层）」，或直接删掉层数细节——它不承载任何吸收决策。

---

## B. 遗漏的前置工程（不是写错，是没写；少了就开不了工）

### B1. 缺「桥接主动推事件给 Core」的通道 —— 最大一块

`MinecraftBridgeClient` 的事件只能通过 `_wait_for(predicate)` 取出，是一问一答模型。而 S1.3 的 `combat.started / combat.ended` 天生是**无人询问也要送达**的事件：现在它们会进入 `self._events` 列表后无人认领，既到不了事件总线，也到不了 UI。

- **锚点**：`agent_companion/core/minecraft_bridge.py:452`（`_wait_for` 是唯一出口）
- **需要**：客户端订阅回调 → `MinecraftGameService` 泵送 → collaboration / event bus → shell
- **依赖它的阶段**：S1.3 战斗感知、S3.1 自主 ticker（全部）

### B2. `state.snapshot` 在 Core 侧没有调用方

bridge 已实现 `state.snapshot.request` 处理，Python 侧也有 payload schema，但**没有任何 Core 代码发送过这条请求**。

- **锚点**：`agent_companion/adapters/minecraft-bridge/index.js:918`（bridge 侧已就绪）；`minecraft_bridge.py` 无对应发送方法
- **依赖它的阶段**：S1.3（往 snapshot 加 `nearby_hostiles`）、S3.1（ticker 的「最近 snapshot」输入）
- **工作量**：小，仿 `_control` 加一个 send + wait 即可

### B3. `_events` / `_seen_output` 无上限增长

目前会话短、目标驱动，不构成问题。加上每 20–60 秒一次的 ticker 与战斗事件流后，长时间会话会持续泄漏；`_wait_for` 每次还要线性扫描整个列表。

- **锚点**：`agent_companion/core/minecraft_bridge.py:161`
- **处理**：与 B1 一起做掉（消费即驱逐 + 上限）

### B4. 一个会话只能有一个 goal，且提交是阻塞调用

`submit_minecraft_goal` 对并发直接返回 `minecraft_goal_already_running`，`MinecraftGameService` 还有 `active_goal` 二次拦截；`submit_goal` 本身阻塞等待（动作超时默认 120s）。自主 ticker 与用户语音指令必然互撞。

- **锚点**：`agent_companion/core/game_adapters.py:289`
- **需要明确写进方案的规则**：用户指令抢占 ticker；ticker 只跳过不排队；ticker 跑独立线程且可取消

---

## C. 建议补进方案（不阻塞开工，但 gate 会问）

1. **自主动作的预算归属**：`max_actions` / `max_blocks_changed` 是用户为**本次会话**批准的额度。ticker 静默消耗会导致「我还没让她做什么，预算就没了」。要么给自主行为单独子预算，要么在审批文案里写明它会花同一份额度。
2. **屏幕观察不能进语音回合的同步路径**：截图 + OCR + 视觉调用是秒级的，塞进实时语音一轮会毁掉延迟。必须按节奏异步采集、缓存最近一份 sanitized 摘要再注入。另：当前配置 `llm.vision_enabled: false`，未配视觉模型时 S1.2 只能退化为纯 OCR，方案里要写明这一分支。
3. **PvP 禁令必须在 bridge 侧也拦一道**：契约层看不到实体类型，「半径内最近的敌对生物」是 bridge 解析的，所以「拒绝以 player 为目标」要在 bridge 与契约两处都成立。

---

## D. §5 战斗默认值：建议方案 A

理由不是保守，是**一致性**：Joi 现在每一个改变世界的动作都有逐动作门禁 + 回执 + no-replay。`attack` 是唯一会主动伤害实体的原语，让它在自主循环里免审批，等于在体系里最危险的动作上开唯一的例外。

方案 A（任何人格下 `attack` 都需用户明确指令，自主循环最多提案 `flee` / `guard` + 台词）已经足以满足切片验收里「胆小人设 = 不主动打、等指令」的演示，且不需要为每个人格建审批矩阵。等 A 在真实服跑通有数据后，B 可单独立项。

---

## E. 已核实无误的部分（不必重复论证）

**Joi 侧声称全部属实**：10 原语无战斗（`minecraft_contract.py:9`）；bridge 中 attack/hostile/mob 零命中；无 `bot.on('chat')`；停会话删 checkpoint（`game_adapters.py:377`）；`createScriptProcessor(2048,1,1)` + 40ms 帧（`realtimeVoice.ts:89`，Qwen 上限 100ms）；`stream_pcm16` 已带 `streaming_mode` + `fragment_interval: 0.15`；`_complete_response` 仅在 `response.done` 发一次 `assistant_text`；realtime 气泡本地生成不落库；`_minecraft_instructions` 目前只收语言参数。

**AIRI 侧声称亦属实**：战斗系统（`services/minecraft/src/skills/combat.ts`）；自主游玩 + 代码生成执行；游戏内聊天为最高优先级输入；prismarine-viewer 网页 POV（3007 端口）；MCP REPL 调试接口；Query DSL（side-effect free 世界查询）；pgvector（PGlite 服务端）+ DuckDB WASM（浏览器端）记忆。

**S1.1 的实现路径可直接沿用**：「`RealtimeVoiceCoordinator` 增加 persona 回调，仿 `voice_locale` / `chat_locale` 的按会话读取模式」——该模式已在 2026-08-15 的语言改造中落地，照抄即可。

---

## F. 过期资料

`docs/AIRI_COMPARISON.md` 已过期（仍写「Windows 优先 / macOS 移植中」「只有 OK-WW 单游戏适配器」），两份新文档都引用了它。它可以留作历史记录，但**不能再作为现状证据**。

---

## G. 建议开工顺序

1. **改字面**：A1、A2、A3（A4 可选）。
2. **S1.0（新增，先做）**：B1 事件推送通道 + B2 snapshot 调用方 + B3 缓冲驱逐。单独验收。
3. **S1.1 / S1.2 / S1.3**：人设注入、屏幕观察、战斗感知。S1.3 依赖 S1.0。
4. **S2 → S3 → S4**：按原方案，S3.1 开工前先把 B4 的抢占规则写定。

## H. 代码评审时的重点检查项

- 协议三处白名单（bridge 输出 / `_safe_observation` / `_validate_event_payload`）是否同步扩展
- 无人认领事件的驱逐与缓冲上限
- ticker 与用户目标的抢占语义，以及 ticker 线程的可取消性
- `attack` 在 bridge 侧的 PvP 拦截（不能只在契约层）
- 自主动作的预算归属与审批文案一致性
- 屏幕摘要：原图不进云端、不落盘
