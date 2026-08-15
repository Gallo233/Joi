# Minecraft 目标切片闭环方案

> 状态：方案（scheme gate 待批）。目标切片由用户定义，本文把切片逐段对照现状、列出缺口并给出分阶段实施方案。
> 关联：`docs/AIRI_ABSORPTION_PLAN.md`（语音/记忆/生态吸收）、`docs/MINECRAFT_REALTIME_P0_P2.md`、`docs/MINECRAFT_REALTIME_P3_P6.md`、`docs/REALTIME_VOICE_DEBUG.md`。

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
| 代码/状态理解意图 | 🟡 部分 | `observe/inventory` 走 mineflayer 状态；但回执只有 4 个数字，无实体/时间/天气 |
| 观察屏幕理解意图 | ❌ 未接入 | 截图+OCR+视觉管线只在 watch/computer-use（`core/tools/screen_observe.py`、`core/watch.py`），Minecraft 会话无此通道 |
| 执行操作 | 🟡 10 原语无战斗 | `minecraft_contract.py::GAME_ACTIONS` 无 attack/flee/guard；bridge 无任何 mob/attack 代码 |
| 人设驱动自主行为 | ❌ | `realtime_voice.py::_minecraft_instructions` 只收语言参数，`CharacterHarness.persona/tone/rules` 未注入；无 Minecraft 自主循环 |
| 主动聊天/询问 | ❌ | 已有可复用件：`watch_loop.py` proactive（台词/emotion/sprite 通道）、`subconscious.py`（空闲主动观察），但都不知游戏状态 |

## 3. 缺口清单

- G1 屏幕观察未接入 Minecraft Skill（管线现成，缺接线）。
- G2 战斗原语与战斗感知完全不存在（契约、服务、桥接三处都要加）。
- G3 人设未注入 Minecraft 实时语音与自主循环。
- G4 无游戏态驱动的自主行为循环。
- G5 无主动聊天/询问（可复用 watch_loop proactive 通道）。
- G6 承接上一轮：realtime 时延未实测、已装 debug App 的 Web 层早于源码、句子级流式 TTS 未做。

## 4. 分阶段实施

### S0 基线（已有，不重做）

配置/审批/进服/实时语音/10 原语/回执/no-replay/owner-bound 全部保留，作为后续所有阶段的底座。

### S1 感知与人设地基（先做，全部后续阶段依赖它）

- **S1.1 人设注入**：`RealtimeVoiceCoordinator` 增加 `persona` 回调（仿 `voice_locale/chat_locale` 的按会话读取模式），`_minecraft_instructions` 拼入 `CharacterHarness` 的 persona/tone/rules 摘要与安全准则（不攻击玩家、坐标不出语音、不承诺未确认结果）。同步给“计划编译器/自主循环”复用同一份人设摘要构造器（`character.py::role_prompt` 已有 persona+tone+rules 格式）。
- **S1.2 屏幕观察通道**：Minecraft 会话激活时，`observe` 之外新增 `observe_screen`（或 observe 的 `evidence: "screen"` 变体）：复用 `tools/screen_observe.py` 的截图+OCR+视觉摘要，结果按既有隐私投影 sanitized 后作为**文本摘要**注入 provider 上下文（云端只收摘要文本，原图/OCR 明细不进云端、不落盘）；麦克风披露文案同步补“屏幕观察”一项。macOS 屏幕录制权限沿用 watch/computer-use 既有路径。
- **S1.3 桥接战斗感知**：`minecraft-bridge/index.js` 监听 `entitySpawn/entityHurt/physicsTick`，`state.snapshot` 增加 `nearby_hostiles`（类型+数量，无坐标）；新增 sanitized 事件 `combat.started/combat.ended`（bot 被敌对生物攻击/脱离）。协议输出 schema 相应扩展（Python 端 `_validate_event_payload` 同步）。

### S2 战斗人格策略

- **S2.1 新原语**：契约层加 `attack`（目标=半径内最近敌对生物）、`flee`（远离敌对生物）、`guard`（原地警戒，无改块）。全走既有 canonicalize/gate/预算/回执；**PvP 永禁**（目标类型契约层强制为 hostile mob，player 直接拒绝）。
- **S2.2 人格策略层（Core 裁决，不交给模型自由裁量）**：harness 增加可选 `minecraft_policy` 字段（角色包可配，缺省用安全默认）。策略表示例：

| 人设 trait | 战斗开始默认 | 明确指令后 |
|---|---|---|
| 胆小/timid | 不提案攻击；自动提案 `flee` + 惊慌台词 + 询问用户“要打吗？” | 允许 `attack`（仍过范围/预算门） |
| 勇敢/brave | 警戒台词 + 提案 `guard`，v1 攻击仍需用户确认（见 §5 选项） | 允许 `attack` |

- 策略表由 Core 解析成结构化规则注入 provider instructions，模型只能按规则提案。

### S3 自主行为与主动聊天

- **S3.1 自主 ticker**：Minecraft 会话激活时，Core 每 20–60s（可配置，带上限）用「人设摘要 + 最近 snapshot + 屏幕摘要 + 世界记忆」组装一次轻量 LLM 调用，输出三选一：`{说话}`、`{行为提案}`、`{无}`。说话走 `safe_voice_line` + GPT-SoVITS + 字幕 + expression 表情；行为提案走 S2 全部门禁。用户语音插话时暂停 ticker（epoch 门禁复用）。
- **S3.2 主动询问**：ticker 允许问题型台词（“那边有怪，要我去看看吗？”“背包快满了，要造个箱子吗？”），复用 `watch_loop.py` 的 proactive_reply/proactive_voice_text/emotion/sprite 通道模式。
- **S3.3 生动性**：新增无改块小动作提案 `wander`（scope 半径内随机路径，pathfinder 已有能力）、`look_at`；台词与表情/口型联动（expression 通道已有）。

### S4 闭环与打磨（承接 `AIRI_ABSORPTION_PLAN.md`）

- V1 句子级流式 TTS、V2 时延实测、V3 AudioWorklet；
- M1 实时会话落库、M2 世界级游戏记忆（含“胆小”等策略偏好记忆）；
- 重建 debug App（Web 层过期问题）、真实服走查全部原语 + 战斗场景。

## 5. 待拍板：战斗策略默认值（v1）

- **方案 A（推荐）**：任何人格下，`attack` 一律需用户明确指令；自主循环最多提案 `flee/guard` 与台词。最安全，Trust & Safety 门最简单。
- **方案 B**：按人设分级——勇敢类人格在 combat.started 时可自主提案 `attack`（仍限敌怪/半径/预算/频次），胆小类仍需明确指令。更生动，但需要逐人格审批与额外测试矩阵。

## 6. 门禁与测试

- 每阶段沿用：`unittest discover`、`test:shell`、`vue-tsc`、`npm run build`、`node --check index.js`、`minecraft_p5_smoke.py`；新增：S1 屏幕摘要隐私测试（原图不进云端/不落盘）、S2 攻击对象矩阵测试（敌怪允许/玩家拒绝/范围外拒绝/预算扣减）、S3 ticker 频率上限与插话暂停测试。
- 真实服 smoke 清单：进服 → 语音对话 → observe_screen 理解意图 → 执行 → 触发 combat.started 验证人格反应（胆小=逃跑+询问，收到指令后攻击）→ ticker 主动搭话。
- 非目标：PvP、自由代码执行、自动重放失败动作、屏幕原图上传云端。

## 7. 风险

- 攻击能力是信任面扩大，必须敌怪-only + 半径/预算/频次三重约束 + 用户明确指令（方案 A 下）。
- 屏幕摘要进云端增加披露面，保持“只摘要、不原图、不落盘”。
- 自主 ticker 增加 LLM 调用成本与打扰风险，频率上限 + 静音开关必须落地。
- 战斗人格策略不能由模型自行发挥，Core 解析结构规则后注入。
