# Joi 共同在场架构

本文件记录 2026-07 的核心能力重塑落地状态。目标是让项目、对话、角色和能力会话成为同一个可恢复上下文，而不是把 Computer Use、陪看、Skill 和游戏做成互不相干的按钮。

## 数据与迁移

`CollaborationStore` 使用 SQLite 保存项目、对话、资源绑定、事件、能力会话、权限、动作回执、Skill 安装/运行/草稿。Tauri 启动时通过 `JOI_DATA_HOME` 指向 macOS 应用数据目录；源码运行和测试继续使用工作区数据目录。

旧 `events.jsonl` 首次启动时会复制为 `.jsonl.pre-sqlite-backup` 并迁入“默认项目 / 原有对话”。迁移标记存于数据库，重复启动不会重复导入。JSONL 暂时保留为兼容审计流，新事件同时写入 SQLite。

所有公开事件都附带：

- `project_id`
- `thread_id`
- `session_id`
- `character_id`
- `public_phase`

`public_phase` 取值为 `idle / received / understanding / thinking / acting / waiting / paused / done / failed`，由 `EventBus` 在 emit 时写入事件顶层字段与 `agent_state`，并单独入库为 events 表的一列。它与 `ui_phase` 的区别在于会让位于能力会话状态：会话暂停时不会继续对外播报 `acting`。

数据库列在首次创建后如有新增，由 `_ensure_columns()` 用 `ALTER TABLE` 补齐；旧库升级不需要重建。

## 能力会话与权限

每次 Computer Use、Scene Session 或 GameAdapter 运行均建立 `CapabilitySession`。会话记录目标、权限档位、驱动、预算、停止条件及状态。

权限语义：

- `observe`：只读。
- `collaborate`：只在项目绑定范围内自动执行。
- `delegate`：跳过项目绑定范围，但绑定当前 Joi 启动会话。

`delegate` 的边界由 `LAUNCH_ID` 实现：授权时把当前进程的启动标识写入 `permission_grants.launch_id`，Joi 重启后从 SQLite 恢复的授权带着旧标识，会以 `delegate_launch_expired` 退回确认，不会静默继续替用户操作。

`CollaborationStore.action_allowed()` 是唯一的自动执行闸门（TDD ADR-005），服务端策略网关直接调用它，不存在第二套并行判断。付款、登录授权、对外发消息、删除、安装和扩权不会因档位升级而跳过确认。

敏感判定分两层，实现在 `agent_companion/core/action_intent.py`：

1. **typed effect（主）**：`ActionIntent.from_request()` 按工具名查 `TOOL_EFFECTS`，得出 `EffectKind`。六种红线 effect 为 `payment / authentication / external_send / deletion / installation / permission_expansion`。未注册的工具落到 `desktop_input`，不会被当作无害。
2. **文本信号（只能升高）**：坐标点击本身不携带语义，因此还会扫描 `ToolRequest.reason` 与 `action`/`intent`/`text`/`url` 等参数——目标定位解析出的候选标签（如"点击候选目标：立即支付"）只存在于 reason 中。

方向是单向的：文本能把 `desktop_input` 升成 `payment`，但**不能**把已 typed 为 `deletion` 的 `files.delete` 说成安全。判定偏向宁可多问一次：多一次确认只是一次点击，漏一次就是真实付款。

`ActionIntent` 同时产出 `normalized_args_digest` 与 `idempotency_key`（对参数顺序稳定、忽略 `memory_context` 等易变键），为后续 schema v3 的 effect lease 与崩溃对账预留。

扩权是独立动作：`permission.expand` 不改变档位，只增加 scope 条目，且必须携带 `confirmed`；未确认时返回将要新增的具体条目供界面展示，确认后写入审计事件。

暂停、继续、取消与用户接管都通过同一状态机处理，接管不会删除目标或动作回执。

## 冷启动恢复

数据库里写着 `running` 只说明"写它的那个进程当时在跑"。崩溃或退出后那个进程已经不在，会话既不能显示为正在执行，也不能自行续跑。

`CollaborationStore` 构造时执行一次 reconciliation 事务（`_reconcile_previous_launch()`）：

- `running` 会话统一转为 `paused`，`pause_reason = recovery_required`；
- `waiting_approval` **保持不动**——它本来就在等人，降级会丢掉那个待决定；
- 上一次启动遗留的 `delegate` 授权标记为 `status=expired` / `status_reason=launch_ended`。

因为 `permission_for_session()` 只返回 `active` 授权，被过期的 delegate 会让会话回落到 observe，任何后续动作都要重新确认——这是构造上的 fail closed，不依赖调用方记得检查。

动作回执在恢复中完整保留；`pause_reason` 持久化在 `capability_sessions` 上，因此恢复出来的会话仍能解释自己为什么停着（进程内的 orchestrator runtime 会随进程消失）。恢复后显式 resume 会清空 `pause_reason`。

对应 PRD-CTX-007、PRD-PLT-006。

## 可恢复 Run（schema v3）

`agent_companion/core/run_store.py` 保存 `runs / run_steps / checkpoints / approval_challenges / effect_attempts / audit_entries / resource_leases / rpc_dedup / deletion_jobs`，与 `CollaborationStore` 共用同一个 SQLite 连接和锁（这些表引用 projects/threads，必须同事务）。现有 v2 表语义未改动。

核心不变量是：**崩溃不能看起来像成功**。

- **不可变 RunContext**：`project/thread/run/session/capability/character` 在 run 创建时冻结。执行中不再读"当前激活对话"补身份，否则排队中的任务会把事件和审批记到用户刚切过去的对话上（ADR-010）。
- **审批是持久化的一次性交易**，不是 UI 布尔值。challenge 记录 `args_hash`/`scope_hash`/`nonce`/TTL/revision；消费时重新校验工具、参数指纹、scope、线程与 TTL。参数被编辑 → `fingerprint_mismatch` 且旧记录转 `superseded`（不修改旧审批）；scope 变了 → `scope_changed`。`approved → consumed` 的 UPDATE 带 `AND status='approved'`，并发第二个消费者拿到 rowcount 0（ADR-004）。
- **CAS effect lease**：`effect_attempts.idempotency_key` 是 UNIQUE。重复 resume 抢不到 insert，会得到原始尝试的状态而不是再执行一次。
- **崩溃对账**：`leased` 无回执 → 重启后标为 `abandoned`，可重新请求决定；`acting` 无回执 → `effect_needs_reconciliation`，只能由人判断外部世界是否已改变，**不自动重放**。
- **audit_entries 是唯一可查询审计账本**，run/step/effect/approval/receipt 互相链接（ADR-011）。

启动 reconciliation 除会话外还处理：`created/running` 的 run 转 `paused/recovery_required`；上次启动残留的 `approved` 审批全部转 `expired`（否则重启后仍可被消费）；清空资源租约；`leased` 无回执的 effect 标为 `abandoned`。

## 并发模型

`RunCoordinator` 用 per-`thread_id` 互斥替代了原来的全局 `_command_lock`：同一对话内严格串行，不同对话并行。但并行不等于可以同时抢桌面——`desktop_input / microphone / speaker` 走 `resource_leases` 独立互斥（TDD §6.6）。

同一 thread 最多一个 active run（`create_run` 返回 `thread_run_active`）；终态 run 不可复活，重试要新建并用 `retry_of` 关联。

执行主干已接入：`app.py` 通过注入式 `RunJournal`（`run_journal.py`）与 store 通信——runtime 不 import store，store 也不认识 planner。`_start_plan` 开 run，`_run_plan` 记 step、在动作前 CAS 取 effect lease、动作后结算，`_make_pending_step` 创建持久化 challenge 并把它的 id 作为交给 Shell 的 `approval_id`，`resolve_approval` 记录决定，`_finish_plan` 收尾。审批在**动作前**才被消费（`spend_challenge`），不是用户点击时——两者之间正是计划可能被改写的窗口。

默认 journal 是空实现：`AgentCompanionApp` 在很多没有数据库的地方被独立构造，用一个 no-op 对象可以让 `app.py` 保持单一代码路径，而不是到处 `if journal is not None`。生产由 `JsonRpcBridge` 注入 `StoreRunJournal`，`tests/test_run_journal.py` 会盯住这一点。

`tests/test_run_lifecycle.py` 覆盖 TDD Phase 1 的全部退出条件：crash-before-act、act-before-receipt、duplicate-resume、过期/指纹不符/scope 变更、同 thread 拒绝第二个 run、跨 thread 身份不串线、并发审批只能有一个赢家。

## 语音代次

语音生产得慢、失效得快。合成与请求它的那一轮是分离执行的，等音频就绪时用户可能已经发了新消息、取消、接管或换了角色。照样播放就是在回答没人还在问的问题，更糟的是让角色对已放弃的工作显得胸有成竹。

`voice_generation.py` 把"一轮用户输入的语音"作为一个代次。新一轮、取消、接管、换角色都会让旧代次退休；为退休代次生成的音频直接丢弃，只记录事实、不记录文本。`_synthesize_voice` 在合成**前后各查一次**——真正的竞态是合成期间用户又说了话。

## 记忆删除传播

`memories_fts` 是 external-content FTS5 表。删除时必须先移除索引再删内容行：内容行一旦消失，FTS 就再也读不到该删哪些 token，旧文本会继续留在索引里被搜到，且任何命中它的查询会以 `missing row from content table` 报错。同理，改写记忆时若不先退休旧 token，上一版文本仍然可搜。

现在三条路径都用 FTS5 的命令式写法（`'delete'` / `'delete-all'`）并调整了顺序。`tests/test_memory_deletion.py` 会 dump 数据库全部表加 Markdown 投影，断言删除后任何地方都不再包含该文本。

**已知缺口**：`memories` 表没有 `project_id`/`thread_id` 列，PRD-AIM-007 要求的按项目/对话作用域召回尚未实现。当前隔离只来自每个角色包各有独立数据库文件。测试里如实记录了这一点，而不是假装它存在。

## 角色表达

角色是用户读取状态的界面。它在会话等着用户时微笑、或在缺权限时一脸平静，这个界面就在说谎——而用户会照着它行动。

所以 `expression_map.py` 让表达由**真实状态推导**，而不是由模型建议。覆盖优先级（PRD §13.3）：

```
approval > takeover > permission_missing > failed > acting > thinking > received > idle
```

模型仍然负责写词，也可以建议语气，但只能在真实状态允许的集合内：`acting` 时可在 `alert/thinking` 之间选，风险态（等待审批、接管、缺权限、失败）则**锁死**，模型给什么都不生效。未经验证的"成功"不算成功，映射为 `failed` 而非 `happy`。

两处顺带修正：

- `express()` 在 `EventBus.emit()` 之前运行，读不到 bus 即将写入的 `public_phase`。新增 `derive_public_phase()` 供两边共用同一份定义，而不是复制一份会漂移的逻辑。
- `paused/waiting_approval` 现在也覆盖 `done`。挂起的会话并没有完成，迟到的完成事件不能在用户还被等着的时候告诉他"活干完了"。`failed` 不被覆盖——失败要保持可见。

## 坐标信任与目标证据

原实现把三个坐标空间压成一个：用**主显示器**宽度除截图宽度得到一个全局 scale，套用到所有显示器。单显示器下恰好正确；接上第二块屏后，副屏窗口的裁剪框会落错位置，clamp 又会把错误伪装成"回退到全屏"——Joi 于是在看主屏、点副屏。

`vision/capture_geometry.py` 把它拆开：

- **logical**：全局桌面点坐标，左侧/上方的显示器原点为负；
- **pixel**：单个显示器的 backing store，Retina 笔记本与 1x 外接屏同一时刻有两个不同 scale；
- **capture**：截图图像内的偏移，原点是被截取的矩形而非桌面。

scale 是**显示器的属性，不是会话的属性**。窗口归属按最大重叠面积判定（跨屏窗口的左上角可能属于邻屏）。无法测量布局时返回 `trusted=False` 而不是猜——没装 PyObjC 但检测到多显示器，就明确报告"知道不止一块屏，但不知道它们在哪"。

`vision/target_evidence.py` 定义 `TargetEvidence`：坐标不是目标，而是"某个目标在某块屏、某个窗口、某张截图、某个时刻"被识别后派生出来的东西。证据携带 `CaptureIdentity`（display 布局 digest、display_id、window_id、app_id、scale、capture digest），并在动作前重新校验。以下任一变化即失效并要求重新观察：显示器增减/移动/改 scale、窗口切换、应用切换、截图内容变化、TTL 过期。

视觉/OCR/坐标来源的目标**永远不能自动执行**——像素能看出一个像按钮的东西，但只有应用 API 或 Accessibility 能确认它就是用户说的那个。歧义、低置信度、不可点击/禁用同样要求用户选择。

模型已接入实际链路：`vision/mac.py` 的截图按窗口所在显示器调用 `screencapture -D <index>`（`screencapture` 一次只写一块屏，从主屏图像里裁副屏窗口无论 scale 多准都是错的），裁剪前先减去该显示器的原点，scale 取该显示器自己的 `backing_scale`。被 clamp 掉的裁剪不再当作"窗口的一个较小视图"——那是另一张图，会连同 `geometry_trusted=False` 一起上报。`CaptureRect` 因此新增 `display_id / display_layout_digest / geometry_trusted`，`targeting.py` 在 `geometry_trusted=False` 时拒绝产出点击坐标。

`DisplayLayoutCache` 给探测加了 5 秒 TTL：Quartz 路径很便宜，但无 PyObjC 时要 shell 出去跑 `system_profiler`（约 1 秒），每次观察都跑不可接受。漏进这个窗口的布局变化由证据里的 layout digest 兜底。

证据的生产者是 `app._record_target_evidence()`：从 targeting 工具已有的 `agent_state` 里读（因此 `targeting.py` 不需要反向依赖证据模块）。目标定位产生的证据只为紧随其后的那次点击背书——`computer.*`/`browser.*` 执行后即清除，否则一次确认过的目标会替计划后面的任意点击担保。

## 绑定范围证明与回执

坐标、键盘和 DOM 动作的参数里不含任何可识别对象，因此"项目存在任意 binding"不再构成授权证据（这是原先的漏洞）。`collaborate` 自动执行现在要求当前 `TargetEvidence` 的 `app_id`/`domain`/`path` 与某条绑定匹配，且证据本身仍然有效。没有证据 → 走审批，而不是等动作后的 focus drift 才发现越界。

回执方面：工具返回 ok 不等于外部世界真的变了。没有动作后观察就没有可比对的东西，回执状态记为 `unverified` 而非 `completed`。

## Computer Use

感知顺序固定为应用 API/DOM、Accessibility、OCR/视觉、坐标兜底。动作改变外部状态后必须再次观察，并生成 `ActionReceipt`。连续无变化、观察签名循环或焦点漂移会暂停会话；步骤、时间、模型调用和失败次数均有预算。

CUA 调用中途失败时只有一个问题重要：**这次点击/按键到底有没有到达应用**。

`computer_use/driver_fallback.py` 据此分类：二进制缺失、daemon 未运行、连接被拒、窗口绑定失败、参数被拒——这些不可能在输入已经在途时发生，判定为 `not_delivered`，原生驱动可以直接补做。超时、进程被杀、管道断开、无法解析的回复——判定为 `uncertain`，**不重试**，改为重新观察并把决定交回给人。第二次点击对"立即支付"来说就是全部问题所在。

无法识别的失败一律按 `uncertain` 处理：多观察一次的代价是一张截图，猜错的代价是一次重复的真实操作。无外部副作用的调用（观察等）则始终可以自由重放。

部分执行的工作流按构造就是 uncertain——有些步骤可能已经生效，绝不整体在原生上重跑。

`FallbackComputerUseBackend` 持有两个驱动并**逐个动作**决策（跑三步在第四步挂掉才是真正要处理的情况），健康的 CUA 会话不会构造原生驱动。注意它是包装器：按类名识别后端的调用方（如 `_is_macos_backend`）必须穿透 `.primary` 看底层，否则会静默改变 macOS 上的动作序列。

Joi 原生 macOS 驱动始终可用。可选 CUA 驱动使用 `cua-driver` 的窗口级截图与后台输入：

1. 只有 `cua-driver status` 成功时才会被自动选择。
2. `get_window_state` 的窗口局部截图直接作为规划坐标空间。
3. 点击、输入、快捷键、滚动和拖拽携带 pid/window_id，并使用 `delivery_mode=background`。
4. 驱动缺失、daemon 未运行或初始化失败时回退原生驱动，不会伪装成后台执行。

## Skill 平台

`AgentSkillService` 支持本地目录、ZIP 和 Git 来源，识别 `SKILL.md`、`scripts/`、`references/` 与 `assets/`。安装预览展示来源、版本、许可证、哈希、依赖、脚本和权限。全局、角色、项目作用域按“项目 > 角色 > 全局”解析。

ZIP 路径穿越、符号链接、未知脚本和安装后哈希变化会被拒绝。代码型 Skill 默认禁用隐式运行，并且只有非观察会话加显式首次确认才能执行。脚本从不导入 Joi 主进程；macOS 使用独立进程和 Seatbelt 配置，默认禁止网络，仅允许声明的项目与运行目录写入。

成功操作只能生成 Skill 草稿，审核输入、步骤与权限后才可以安装。草稿在设置页"待审阅草稿"面板列出，展示步骤后由用户选择作用域安装或丢弃；未审核的草稿不会安装，也不会运行。

已知平台缺口：脚本沙箱依赖 macOS Seatbelt，非 darwin 平台上 `skill.run` 返回 `sandbox_runner_unavailable`，脚本型 Skill 不可执行。

## Scene Session

陪看默认为“安静共看”。Scene Session 比较画面、字幕、转写与章节变化，只在模式和事件显著度允许时评论。支持解说、翻译、分析、无障碍描述、剧透等级与语音打断。

默认不保留原始视频和音频；会话只保存用户允许的摘要、书签和记忆候选。截图仍受现有临时视觉工件清理策略管理，不作为长期媒体库存档。

## GameAdapter

GameAdapter manifest 声明检测、观察源、动作集、暂停、验证、存档点和平台。安装、启停与卸载状态独立于角色包。

- OK-WW 已包装为经过审查的可安装适配器，保留 dry-run 和显式授权。runner 路径只能来自 `OK_WW_RUNNER` 环境变量，没有内置默认值；未配置、路径无效或非 Windows 平台都会返回 `setup_required` 并给出对应的 setup hint。解析逻辑集中在 `agent_companion/core/ok_ww.py`，适配器检测、技能清单与工具执行共用同一份。
- Minecraft bridge 使用 JSON Lines 协议与 Mineflayer，支持独立伙伴的跟随、探索、采集、建造、断线重连、暂停和 checkpoint。
- 角色接管模式会监听控制文件；用户输入或接管标记出现时立即暂停。

桥接依赖不会静默安装；用户必须先完成适配器安装预览与权限确认。

## 主要 RPC

- `project.*`、`thread.*`、`resource_binding.*`
- `capability.session.start/status/pause/resume/cancel`
- `permission.grant/revoke/expand`
- `action_receipt.list`
- `skill.inspect/install/update/uninstall/validate/run/draft.*`
- `game.adapter.*`

旧 `conversation.history`、`watch.loop.*`、`computer.workflow` 与 `game.ok_ww.run` 仍可调用，但已通过新上下文与能力会话记录状态。

## 验证入口

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python run_agent_companion_tests.py
cd agent_companion/shell && npm run build
cd agent_companion/shell/src-tauri && cargo check
node --check agent_companion/adapters/minecraft-bridge/index.js
```

CI 按 TDD §15.2 分为两条 lane：`macos-required` 是主平台必过项（单元/契约/迁移/恢复测试、Shell typecheck+build、cargo check、sidecar 握手 smoke、发布隐私扫描），`windows-compatibility` 只验证共享契约与 Windows 打包工具，不能替代 macOS 结论。

`tests/test_protocol_contract.py` 冻结 Core↔Shell 边界：JSON-RPC 信封与错误码、`AgentEvent` 字段集、`public_phase` 词表、`ToolResult` 五通道、SQLite schema 与旧库迁移，并逐项校验 `protocol.ts` 的枚举与 Core 一致——任一侧漂移都会失败。
