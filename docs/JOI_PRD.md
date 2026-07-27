# Joi 产品需求文档（PRD）

> 文档状态：Joi 1.0 macOS-first 产品基线草案（Director rework 已合入，待 TDD closeout）<br>
> 研究快照：2026-07-26<br>
> 产品负责人：Joi Studio / Product Design Director<br>
> 执行角色：Product & Experience Designer<br>
> 配套技术设计：[JOI_TDD.md](JOI_TDD.md)

## 1. 文档目的

本 PRD 把 Joi 当前已构建的能力收束成一个明确、可验收、可发布的 macOS-first 产品。它不是从零设想新产品，也不把现有功能清单当作产品定义；它回答：

- Joi 为谁解决什么问题；
- 为什么 Joi 不等同于聊天机器人、通用 AI OS 或自动化脚本；
- 已有能力如何组合成稳定的产品旅程；
- Joi 1.0 必须做到什么、明确不做什么；
- 产品、技术、安全、美术和质量如何判断“可以收口”。

本文中的“现有”指 2026-07-26 当前工作树可见实现。当前工作树包含未提交开发内容，因此所有发布状态仍以实际测试、签名、安装和真机验收为准。

## 2. 产品摘要

Joi 是一个 **macOS-first、本地优先、角色化、可审计的多模态 Agent Companion**。

Joi 1.0 首先服务于长期在 IDE、浏览器和终端中工作的 Apple Silicon Mac 开发者与创作者。它以一个可随时收起的角色界面维持项目上下文，并在用户明确授权后完成范围受限、可暂停、可验证的桌面协作。聊天、项目上下文、一次可靠的“观察—行动—验证”闭环，以及由用户确认的长期记忆，是 1.0 的发布主线；陪看、第三方 Skill、游戏、Coding Agent takeover 和高级角色生态按独立成熟度进入 Beta。

Joi 的核心不是“有一个会说话的角色”，而是让角色成为高能力 Agent 的信任与表达界面：

```text
看见环境 → 理解用户目标 → 提出方案 → 获得授权 → 执行动作
→ 验证结果 → 留下审计 → 用适合人的方式表达
```

Joi 要同时具备三种品质：

1. **在场感**：理解当前项目、对话、屏幕、角色和正在进行的能力会话。
2. **行动力**：能调用工具、Computer Use、Coding Agent、Skill 和游戏适配器完成工作。
3. **可信度**：用户始终知道 Joi 在做什么、为什么需要确认、做完后是否真的成功。

一句话定位：

> Joi 是住在 Mac 上、能看、能听、能记、能做事，同时把授权和结果说清楚的 AI 伙伴。

## 3. 产品边界

### 3.1 Joi 是什么

- 面向个人用户的桌面 AI companion。
- 面向开发、创作、观看、游戏和轻量桌面任务的能力宿主。
- 以项目、对话和角色为长期上下文，以能力会话为执行边界。
- 默认本地保存项目状态、记忆、审计和角色包。
- 支持 BYOK 和多模型路由，不绑定单一模型供应商。

### 3.2 Joi 不是什么

- 不是无限权限的自主 AI OS。
- 不是只展示 Live2D/VRM 的聊天皮肤。
- 不是把全部个人数据自动上传云端的“全知助手”。
- 不是以集成数量为优先的 OAuth 聚合器。
- 不是绕过 macOS 权限、用户确认或应用安全边界的自动点击器。
- 不是通用工作流平台、企业 RPA 或社交平台机器人中台。

## 4. 用户与核心任务

### 4.1 首发用户

| 层级 | 用户 | 典型环境 | 核心需求 |
|---|---|---|---|
| Primary | Apple Silicon Mac 开发者与创作者 | 长时间在 IDE、浏览器、终端和素材工具间切换 | 项目连续性、屏幕理解、受控行动和可验证结果 |
| Secondary | AI companion 爱好者 | 重视角色、声音、记忆和长期关系 | 角色连续性、可控记忆、自然表达和真实在场感 |
| Beta | 游戏与内容用户 | 观看视频、直播或玩支持的游戏 | 安静共看、事件评论、游戏辅助和随时接管 |
| 约束画像 | 隐私敏感的高级用户 | 使用 BYOK、本地兼容端点或私有项目 | 数据可见可删、明确授权、无隐式上传 |

### 4.2 Jobs to be done

1. 当我在一个项目中工作时，我希望 Joi 记得项目、对话和相关资源，不必每次重新解释。
2. 当我让 Joi 操作电脑时，我希望先知道它要做什么，并能暂停、拒绝或接管。
3. 当 Joi 执行后，我希望看到它是否真的改变了目标状态，而不只是“声称完成”。
4. 当我与角色长期相处时，我希望记忆由我控制，角色表现连续但不过度主动。
5. 当模型、OCR、语音或外部工具不可用时，我希望 Joi 诚实降级，而不是伪装成功。
6. 当我安装 Skill、角色包或游戏适配器时，我希望先看到来源、权限、脚本和影响。

## 5. 产品目标与非目标

### 5.1 Joi 1.0 目标

- 交付可在 Apple Silicon macOS 上独立安装、启动和更新的桌面应用。
- 建立“项目—对话—角色—能力会话”统一上下文。
- 完成基础 AI 对话、项目上下文、受限 Computer Use、用户确认记忆和默认角色的首发闭环。
- 对外部状态变化提供审批、作用域、预算、暂停、验证和审计。
- 让角色的视觉、声音和情绪与真实运行状态一致。
- 在未配置模型或缺少系统权限时仍提供可理解的降级体验。
- 建立从 PRD 需求到 TDD 模块、测试和发布门禁的可追踪关系。

### 5.2 首发 Hero Journey

```text
安装 → 完成基础配置 → 与默认角色进行一次真实 AI 对话
→ 进入项目 → 观察一个可信窗口
→ Joi 提出一个非敏感动作 → 用户逐步确认
→ Joi 执行并重新观察验证 → 生成一条待确认记忆
→ 用户确认、编辑或拒绝该记忆
```

这条旅程是 1.0 的发布楔子。Watch/Scene、第三方 Skill 安装、游戏适配器、完整角色 CRUD 和 Coding Agent takeover 不得阻塞首发主线；它们按 Beta 轨道独立验收。

### 5.3 非目标

- 1.0 不承诺 iOS、Android、Linux 或 Intel Mac 正式支持。
- 1.0 不承诺无确认的长时间全自主执行。
- 1.0 不建立公共 Skill/角色资产市场。
- 1.0 不以大量第三方 SaaS 集成为发布前提。
- 1.0 不承诺第三方 Skill、游戏或 Coding Agent takeover 达到公开稳定等级。
- 1.0 不自动保存原始屏幕视频、连续音频或全部 OCR/转写。
- 1.0 不保证所有 canvas、游戏或自绘 UI 都能可靠语义定位。

## 6. 产品设计原则

1. **macOS-first，不是 macOS-only**：主体验和发布以 macOS 为准，共享契约保留 Windows 适配。
2. **角色即信任界面**：表情、语音、任务卡和审批必须表达真实系统状态。
3. **先观察，再行动，再验证**：没有可信观察和动作回执就不能宣称完成。
4. **本地优先、云端可替换**：本地状态是默认事实源；外部模型和服务是可替换依赖。
5. **渐进授权**：观察、协作、委托是不同能力，不通过模糊开关合并。
6. **记忆需要治理**：候选、确认、编辑、删除、禁用和作用域必须可见。
7. **失败也要有产品体验**：权限缺失、模型不可用、焦点漂移和结果不确定都有明确状态。
8. **扩展先审查**：Skill、角色包和适配器先 inspect/dry-run，再安装或执行。
9. **少而完整**：优先把核心旅程做深，不用集成数量或模型数量掩盖基础闭环缺口。

## 7. 开源参考与取舍

研究只用于产品与架构模式提炼。Joi 不复制第三方角色、美术、品牌、提示词或未经审查的代码。

| 项目 | 一手资料 | 借鉴 | Joi 不照搬 |
|---|---|---|---|
| AIRI | [GitHub](https://github.com/moeru-ai/airi)、[官方概览](https://airi.moeru.ai/docs/zh-Hans/docs/overview/) | Stage/Core 分离、Live2D/VRM、多模态语音、插件 Kits/Tools/Gamelet、IO Tracer | 不追求多 Stage/多端并行扩张；不把实验性插件 UI 直接带入高权限执行 |
| OpenHuman | [GitHub](https://github.com/tinyhumansai/openhuman) | UI-first onboarding、桌面 mascot、本地 Memory Tree、Markdown/Obsidian 可读记忆、跨线程连续性 | 不在 1.0 追求百项 OAuth 集成；不把“本地”与托管服务混为一谈 |
| Letta | [Memory Blocks](https://docs.letta.com/v1-sdk/memory/memory-blocks)、[GitHub](https://github.com/letta-ai/letta) | Stateful Agent、显式 memory blocks、长期身份与上下文管理 | 不把 Agent 可写 memory block 直接等同于终端用户同意；不允许未经治理地自改人格、系统提示或敏感记忆 |
| LangGraph | [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)、[Human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop) | 持久化 checkpoint、可恢复 interrupt、approve/edit/reject、恢复前副作用幂等 | 不为采用框架而重写 Joi；吸收状态机和恢复语义即可 |
| OpenHands | [GitHub](https://github.com/OpenHands/OpenHands)、[Runtime Architecture](https://docs.openhands.dev/openhands/usage/architecture/runtime) | Agent 与隔离执行环境分离、工作区挂载、运行时生命周期和事件流 | 不要求桌面用户安装 Docker；macOS Skill 使用轻量独立进程与系统沙箱 |
| elizaOS | [GitHub](https://github.com/elizaOS/eliza) | runtime / project / plugin / app surface 分层，actions/providers/services 插件模型 | 不复制庞大连接器生态或把产品壳与框架治理混在一起 |

### 7.1 对 Joi 的结论

一手资料显示下列模式已被真实项目采用，提供了可验证的设计证据；是否适合 Joi 仍由其 macOS-first、隐私、授权和发布约束决定。研究快照以本文日期及所链接的官方页面为准，不把第三方实验路线视为 Joi 的 1.0 承诺。

- AIRI 为“角色舞台”和“Agent Core”独立演进提供参考，但其 Computer Use、插件宿主、游戏智能体和完整长期记忆仍不应被当作成熟性背书。
- OpenHuman 为本地 Memory Tree、可读 Markdown 与托管服务边界提供参考。
- Letta 为显式结构化 memory block 提供参考；终端用户同意、敏感分类和保护块仍由 Joi 自行治理。
- LangGraph 为持久化 checkpoint、可恢复审批与幂等副作用提供参考。
- OpenHands 为代码型扩展与宿主之间的隔离执行边界提供参考。
- elizaOS 为动作、上下文提供者、长生命周期服务和产品表面的扩展分层提供参考。

## 8. 当前基础与主要缺口

| 能力 | 当前基础 | 1.0 收口重点 |
|---|---|---|
| 桌面壳 | Tauri 2 + Vue 3，透明窗口、macOS 标题栏、Sidecar 草案 | 干净机器安装、启动恢复、签名公证、权限 onboarding |
| 角色 | 静态角色、Live2D/VRM runtime、角色包 CRUD | 品牌资产定稿、角色状态规范、性能/降级、资产授权 |
| 上下文 | SQLite 项目、对话、资源、事件、能力会话 | UI 信息架构、迁移稳定性、冷启动恢复 |
| Computer Use | macOS 原生驱动、Accessibility、OCR/视觉、可选 CUA | 多显示器 Retina、真实任务基准、可恢复审批 |
| 陪看 | Watch/Scene Session、安静模式、OCR/视觉摘要 | 字幕/事件稳定性、打断规则、真实视频体验 |
| 记忆 | SQLite/Markdown、候选、召回、编辑删除、subconscious；当前记录尚无完整 user/project/thread/character scope | 默认确认策略、作用域迁移、冲突处理、质量评估、备份导出 |
| Skill | inspect/install/update/run/draft，项目/角色/全局作用域 | 签名/信任模型、依赖解释、非 macOS runner 策略 |
| Coding Agent | Codex/Agent CLI takeover、审计桥 | 可恢复权限、工作区边界、明确终态 |
| 安全审计 | Policy、审批指纹、持久审计、ActionReceipt | checkpoint、审计 UI 收口、真实攻击/误操作用例 |
| 发布 | macOS workflow、Core sidecar、资产哈希、DMG 草案 | 软件许可证、第三方权利、正式隐私文本、干净机验收 |

## 9. 产品信息架构与桌面在场

### 9.1 渐进式信息架构

Joi 1.0 延续当前壳的渐进披露，不要求为文档概念一次性重建七个一级导航：

1. **Workspace**：默认角色、当前项目/对话、任务卡和能力会话控制。
2. **Conversation**：普通对话、项目协作和运行结果；审批卡与普通消息视觉分离。
3. **Library**：Memory、Characters，以及 Beta 阶段的 Skills & Integrations。
4. **Settings**：BYOK/本地兼容端点、语音、OCR、隐私、macOS 权限和开发者诊断。

Projects 属于 Workspace 的上下文导航；Sessions 属于项目/对话中的任务状态，而不是默认一级入口。开发者事件、原始标识、审计细节和运行时诊断仅在 Developer Mode 展示。

### 9.2 Desktop Presence

Joi 有两个互补表面：

| 表面 | 职责 | 交互边界 |
|---|---|---|
| 主工作区 | 对话、项目、审批、记忆、设置和完整审计 | 常规可调整窗口；关闭窗口与退出应用必须明确区分 |
| Companion Overlay | 角色在场、简短状态、当前任务与恢复入口 | 可移动、可收起、可关闭 always-on-top；不得遮蔽审批或成为唯一退出路径 |

当前壳已有 compact/always-on-top 基础；1.0 必须定义并验证：

- Dock、关闭窗口、隐藏角色、退出应用和重启恢复的不同语义；
- Overlay 在多显示器、Spaces 和全屏应用间的保存位置与可信捕获范围；
- 后台屏幕/麦克风观察的持续指示、暂停入口、安静时段和能耗预算；
- reduced-motion 与 VoiceOver 下等价的状态、任务和恢复入口；
- 点击穿透和自动淡出若未完成可靠恢复机制则保持关闭；它们不属于 1.0 P0。

多显示器或坐标信任不足时，Joi 必须停止动作，并引导用户将目标窗口移动到受支持范围；透明或置顶状态不得让用户失去接管能力。

### 9.3 1.0 模型路径

1.0 冻结为 **BYOK-first、无默认托管推理服务**：

- 用户可配置受支持的外部 provider，或连接本地兼容端点；安装包不捆绑大模型。
- 无 provider 时，Joi 仍可进入 Workspace、管理本地数据、检查权限并演示确定性能力，但规则回复不得计为“成功 AI 对话”。
- 首次配置必须说明文本、截图、音频或代码分别会发送到哪个 provider；未经能力请求不发送相关模态。
- 未来默认托管服务属于独立产品与隐私决策，不通过远程配置静默开启。
- 激活指标按“已有 provider / 新配置 provider / 无 provider”分别统计。

## 10. 核心用户旅程

### 10.1 首次启动

1. 启动 Joi，Core Sidecar 完成认证握手。
2. Joi 说明本地数据、BYOK 外发与本地兼容端点的边界。
3. 用户配置 provider，或明确选择稍后配置；后者不伪装成完整 AI 对话。
4. 仅在首次使用相关能力时请求麦克风、Accessibility、Screen Recording。
5. 已配置 provider 的用户完成一次基础 AI 对话与角色呈现。
6. 权限或模型缺失时仍可进入可用的降级状态。

### 10.2 项目内协作

1. 用户创建/进入项目。
2. 选择对话和角色，绑定文件夹、应用、域名或游戏资源。
3. Joi 读取允许的项目上下文，规划任务。
4. 用户看到任务卡、进度和需要确认的步骤。
5. 完成后结果、审计和可选记忆回到同一项目/对话。

### 10.3 Computer Use

1. 用户提出目标，而不是提供坐标。
2. Joi 按 API/DOM → Accessibility → OCR/视觉 → 坐标兜底观察。
3. 对歧义目标展示候选；对敏感动作要求明确确认。
4. 会话按 `observe / collaborate / delegate` 权限档位运行。
5. 用户可暂停、继续、取消或接管。
6. 每个外部动作后重新观察并生成 ActionReceipt。
7. 无变化、循环、焦点漂移或预算耗尽时自动暂停。

### 10.4 安静共看

1. 用户进入 Scene Session，默认模式是“安静共看”。
2. Joi 观察画面、字幕和可用转写。
3. 仅在事件显著、用户询问或所选模式允许时评论。
4. 原始视频/音频默认不长期保存。
5. 用户可切换解说、翻译、分析、无障碍描述和剧透等级。

### 10.5 记忆

1. 聊天、任务、观看或 Skill 产生记忆候选。
2. 默认只生成候选，不自动写入长期记忆；只有用户显式为某一低风险类型开启自动保存时才可例外。
3. 用户可查看内容、类型、来源、作用域、置信度、敏感性结果和保存原因。
4. 用户可接受、编辑、拒绝、删除、清空或完全禁用记忆。
5. 后续召回只注入与当前项目/角色/用户目标相关的最小上下文。
6. 人格、系统政策和用户锁定事实属于保护块，模型不得自行修改。

### 10.6 Skill 与角色扩展

1. 用户选择本地目录、ZIP 或 Git 来源。
2. Joi 展示来源、许可证、哈希、脚本、依赖、权限和作用域预览。
3. 代码型 Skill 默认不允许隐式执行。
4. 成功任务可生成 Skill 草稿，但草稿必须审核后才能安装。
5. 角色包独立于 Skill；角色人格、资产和能力授权不能混成一个不可审查包。

## 11. 功能需求

优先级定义：

- **P0**：macOS 1.0 发布阻塞。
- **P1**：1.x 产品完整性。
- **P2**：明确延期。

### 11.1 平台与首次运行

| ID | 优先级 | 需求 |
|---|---|---|
| PRD-PLT-001 | P0 | 提供 Apple Silicon macOS 12+ 独立安装包，用户无需 Python、Node、Rust 或源码目录。 |
| PRD-PLT-002 | P0 | Tauri 必须启动经认证的 Core Sidecar，并使用每次启动随机 loopback 端口和会话凭据。 |
| PRD-PLT-003 | P0 | 首次启动必须解释本地数据、BYOK 与本地兼容端点边界，并逐模态说明文本、截图、音频或代码将发往哪个 provider；1.0 不默认开启托管推理。 |
| PRD-PLT-004 | P0 | Accessibility、Screen Recording、麦克风和 Apple Events 按需请求；对 not-determined/denied/restricted/granted/revoked-at-runtime 均显示恢复步骤与仍可用能力。 |
| PRD-PLT-005 | P0 | 启动失败必须提供不含秘密、提示词或私有内容的可操作诊断。 |
| PRD-PLT-006 | P0 | 应用关闭/重启后恢复项目、对话、角色和持久 checkpoint；旧 running/executing 会话进入 recovery-required，恢复前不得重复副作用，启动级委托权限必须失效。 |
| PRD-PLT-007 | P0 | 提供应用内更新检查、版本和变更摘要，更新失败不破坏本地数据。 |

### 11.2 项目、对话与共同在场

| ID | 优先级 | 需求 |
|---|---|---|
| PRD-CTX-001 | P0 | 用户可创建、重命名、归档和删除项目。 |
| PRD-CTX-002 | P0 | 每个项目支持多个对话、默认角色和绑定资源。 |
| PRD-CTX-003 | P0 | 公开事件必须携带 project/thread/session/character identity 和 public phase。 |
| PRD-CTX-004 | P0 | 同一对话同一时间只允许一个主动 run；不同对话的并发不得混淆事件和审批。 |
| PRD-CTX-005 | P0 | 能力会话支持 start/status/pause/resume/cancel/takeover。 |
| PRD-CTX-006 | P0 | 项目绑定范围可包含目录、应用、域名和游戏，并可查看、扩展和撤销。 |
| PRD-CTX-007 | P0 | 冷启动恢复不得把已暂停/等待确认的会话展示为正在执行。 |
| PRD-CTX-008 | P1 | 用户可导出项目的人类可读摘要，不包含秘密和原始私有媒体。 |

### 11.3 对话、角色与表达

| ID | 优先级 | 需求 |
|---|---|---|
| PRD-CHR-001 | P0 | 普通对话、任务、审批和开发者事件在视觉层级上清晰区分；普通界面不得暴露 sprite/model/provider/internal-state ID。 |
| PRD-CHR-002 | P0 | 角色的情绪、动作、语音和 public phase 必须与真实运行状态一致。 |
| PRD-CHR-003 | P0 | 新用户在无额外角色资产时仍有合法、可发布的默认角色体验。 |
| PRD-CHR-004 | P1 | 支持角色创建、导入、检查、预览、激活、复制、更新、导出和卸载。 |
| PRD-CHR-005 | P0 | Live2D/VRM 加载失败时显示稳定降级，不影响任务、审批或设置。 |
| PRD-CHR-006 | P0 | 新用户意图可打断旧语音；过期音频不得在新任务后播放。 |
| PRD-CHR-007 | P0 | `voice_line` 不得朗读路径、坐标、原始输入、JSON、命令、模型名、token、日志或内部 ID。 |
| PRD-CHR-008 | P1 | 用户可分别配置角色外观、人格、声音和主动程度，且权限不随角色包隐式提升。 |

### 11.4 观察、陪看与感知

| ID | 优先级 | 需求 |
|---|---|---|
| PRD-SEE-001 | P0 | 支持 macOS 当前窗口/屏幕观察及清晰的 Screen Recording 权限状态。 |
| PRD-SEE-002 | P0 | 感知优先级固定为应用 API/DOM、Accessibility、OCR/视觉、坐标兜底。 |
| PRD-SEE-003 | P0 | OCR/视觉模型不可用时给出可理解的能力差异，不阻塞其他功能。 |
| PRD-SEE-004 | P0 | 语义目标候选展示来源、置信度、歧义、可行动性和可信捕获范围摘要。 |
| PRD-SEE-005 | P1 | Scene Session 默认安静，只在模式、显著事件或用户请求允许时评论。 |
| PRD-SEE-006 | P1 | Watch/Scene 默认不长期保存原始视频和音频。 |
| PRD-SEE-007 | P1 | 支持解说、翻译、分析、无障碍描述、剧透等级和语音打断。 |
| PRD-SEE-008 | P0 | 对多显示器 Retina、窗口移动、缩放和焦点切换提供真实设备验证；无法建立 per-display/window 坐标信任时 fail closed。 |

### 11.5 行动与能力会话

| ID | 优先级 | 需求 |
|---|---|---|
| PRD-ACT-001 | P0 | 支持 observe、collaborate、delegate 三种权限档位，并清晰解释差异。 |
| PRD-ACT-002 | P0 | delegate 只对当前 Joi 启动有效，重启后必须重新确认。 |
| PRD-ACT-003 | P0 | 支付、登录授权、对外发送、删除、安装、权限档位升级和资源绑定新增/扩展始终通过一次性审批交易显式确认。 |
| PRD-ACT-004 | P0 | 目标歧义、视觉-only 目标或不可信坐标必须先选择/确认，不得自动点击。 |
| PRD-ACT-005 | P0 | 点击、双击、输入、快捷键、滚动、拖拽、打开应用和工作流均通过统一动作边界。 |
| PRD-ACT-006 | P0 | 每个改变外部状态的动作后必须再次观察并生成 ActionReceipt。 |
| PRD-ACT-007 | P0 | 连续无变化、循环、焦点漂移、失败或预算耗尽必须暂停或失败关闭。 |
| PRD-ACT-008 | P0 | 用户可在能力会话中暂停、继续、取消或接管，且不会丢失已完成回执。 |
| PRD-ACT-009 | P0 | 可选 `cua-driver` 只在状态探测成功时选用，失败必须回退原生驱动并说明。 |
| PRD-ACT-010 | P0 | 等待确认的动作可在应用重启后安全恢复、编辑或拒绝；恢复前复验身份、作用域和外部状态，并以幂等 effect key 防止重复副作用。 |
| PRD-ACT-011 | P2 | 经基准验证后允许在限定范围内批量协作动作；敏感动作仍逐步确认。 |

### 11.6 AI、记忆与语音

| ID | 优先级 | 需求 |
|---|---|---|
| PRD-AIM-001 | P0 | 支持 fast/reasoning/vision/code/summarize/voice_style 等稳定模型路由标签。 |
| PRD-AIM-002 | P0 | 未配置模型时使用规则或本地降级路径，并明确说明能力限制。 |
| PRD-AIM-003 | P0 | Provider 状态只显示安全元数据，不暴露 API key、endpoint、模型路径或原始错误。 |
| PRD-AIM-004 | P0 | 记忆候选区分偏好、事实、项目摘要、任务结果和关系注记，并记录来源。 |
| PRD-AIM-005 | P0 | 默认只生成候选且仅用户确认后写入；用户可查看类型、来源、作用域、置信度与保存原因，并可接受、编辑、拒绝、删除、清空和禁用记忆。 |
| PRD-AIM-006 | P0 | 屏幕观察、原始 OCR、截图、日志和原始转写默认不进入长期记忆。 |
| PRD-AIM-007 | P0 | 记忆召回按用户/项目/对话/角色作用域、同意状态和上下文预算限制；保护块不可由模型自行修改，召回需能解释原因。 |
| PRD-AIM-008 | P0 | ASR/TTS 支持大小、时长、超时、取消和迟到事件防护。 |
| PRD-AIM-009 | P1 | 提供人类可读 Markdown 记忆视图及导出/备份，数据库仍保持规范事实源。 |
| PRD-AIM-010 | P0 | 对记忆质量建立可复现实验，至少衡量项目隔离、正确召回、错误注入、冲突、敏感拒绝和删除后零召回。 |

### 11.7 Skill、工具与集成

| ID | 优先级 | 需求 |
|---|---|---|
| PRD-SKL-001 | P0 | 内建能力必须有稳定 Skill ID、输入/输出 Schema、权限、dry-run、状态和审计策略。 |
| PRD-SKL-002 | P1 | Agent Skill 支持本地目录、ZIP 和 Git 来源的 inspect/validate。 |
| PRD-SKL-003 | P1 | 安装前展示来源、版本、许可证、哈希、依赖、脚本、权限和作用域。 |
| PRD-SKL-004 | P1 | ZIP 穿越、符号链接、未知脚本和安装后哈希变化必须拒绝。 |
| PRD-SKL-005 | P1 | 代码型 Skill 默认禁止隐式运行，并在独立进程/最小能力沙箱中执行，不得默认获得全盘读取、宽泛进程或网络权限。 |
| PRD-SKL-006 | P1 | Skill 作用域按项目 > 角色 > 全局解析，且可禁用、更新、卸载。 |
| PRD-SKL-007 | P1 | 成功操作只能生成待审核 Skill 草稿，不得自动安装或运行。 |
| PRD-SKL-008 | P1 | Codex/Agent CLI、浏览器、MCP、文件和游戏适配器遵守同一政策、审批与统一 effect 审计边界。 |
| PRD-SKL-009 | P1 | 插件可贡献工具和受限 UI，但不得绕过 Core Schema、Policy 或安全投影。 |
| PRD-SKL-010 | P2 | 公共 Skill/角色目录在签名、审核、撤回和版本治理完成后再开放。 |

### 11.8 信任、安全、可访问性与发布

| ID | 优先级 | 需求 |
|---|---|---|
| PRD-TRU-001 | P0 | 默认数据保存在本机应用数据目录；任何外发都必须可解释。 |
| PRD-TRU-002 | P0 | 审批绑定 task/step/tool/规范化参数指纹/TTL，只能使用一次。 |
| PRD-TRU-003 | P0 | 权限拒绝、过期、指纹不匹配、作用域不符和恢复失败必须 fail closed。 |
| PRD-TRU-004 | P0 | UI、planner、voice、memory 和 audit 使用分离的数据投影。 |
| PRD-TRU-005 | P0 | 用户可查看近期动作、审批、验证和策略阻止记录。 |
| PRD-TRU-006 | P0 | 发布包不得包含配置、秘密、运行数据、日志、截图、模型文件或开发依赖。 |
| PRD-TRU-007 | P0 | 键盘、VoiceOver、对比度、缩放和 reduced-motion 覆盖核心旅程。 |
| PRD-TRU-008 | P0 | 公共发布前完成软件许可证、第三方通知、默认角色/字体/图标/声音/Live2D 权利链、可再分发证明和隐私文本。 |
| PRD-TRU-009 | P0 | 签名、公证、Gatekeeper、干净机安装和权限撤销流程必须真机通过。 |
| PRD-TRU-010 | P0 | 提供本地数据导出与彻底删除，并验证删除后的召回、索引和备份行为。 |

## 12. 产品状态与权限语义

### 12.1 Public phase

Joi 对用户公开的状态：

```text
idle → received → understanding → thinking → acting
                       ↘ waiting / paused
                  done / failed
```

能力会话状态优先于临时 UI 动画。暂停中的会话不得因为迟到事件重新显示为 `acting`。

### 12.2 权限档位

| 档位 | 含义 | 自动执行范围 |
|---|---|---|
| observe | 只读观察 | 不改变外部状态 |
| collaborate | 项目内协作 | 仅在明确绑定范围和非敏感动作内 |
| delegate | 当前启动内委托 | 可超出项目绑定，但敏感动作仍确认；重启失效 |

权限档位不是对支付、登录、发送、删除、安装或扩权的豁免。

### 12.3 macOS 系统权限状态

| 状态 | 用户看到什么 | Joi 行为 | 恢复 |
|---|---|---|---|
| not-determined | 请求前用途、涉及数据与可跳过项 | 仅在用户触发相关能力后请求 | 用户继续或跳过 |
| denied | 被拒绝的具体权限与仍可用能力 | 相关能力降级，不循环弹窗 | 打开对应 System Settings 后 recheck |
| restricted | 系统/管理策略限制说明 | fail closed | 提示由设备策略管理员处理 |
| granted | 正在使用时的持续指示 | 只在会话与预算内使用 | 用户可随时暂停 |
| revoked-at-runtime | 权限已失效、当前任务已暂停 | 丢弃旧坐标/捕获并停止相关 adapter | 重新授权、recheck，必要时按系统要求重启 |

Accessibility、Screen Recording、Microphone 和 Apple Events 必须分别呈现，不合并成“全部允许”。

## 13. 角色与美术方向

### 13.1 角色原则

- 角色必须表达真实状态，而不是永远保持“开心/忙碌”。
- 主动表达要克制；默认共看不抢内容。
- 等待审批、失败、暂停、接管和不确定状态必须有清晰但不过度戏剧化的表现。
- 视觉和声音不能掩盖风险、错误或用户控制。

### 13.2 1.0 必备状态

- idle / available
- listening / received
- understanding / thinking
- acting
- waiting for approval
- paused / taken over
- success
- failed / needs setup

每个状态需定义表情、姿态、动效、语音可否播放、任务卡语义和 reduced-motion 降级。

### 13.3 状态映射与 Art Bible

角色运行时只使用六种规范 emotion：

```text
neutral / happy / thinking / alert / worried / serious
```

Art Director 必须交付 `public phase × capability substate × risk state → expression / motion / speech eligibility / card semantics` 的映射。覆盖优先级固定为：

```text
approval > takeover > permission-missing > failed
> acting > thinking > received > idle
```

因此，等待审批或高风险状态不得被迟到的成功动画、模型建议情绪或装饰动作覆盖。

进入 Art closeout 前还需提交：角色轮廓与色彩/材质、表情强度、姿态语法、空闲动作频率、状态过渡、lip-sync、主工作区/Overlay/审批卡关键帧、dark/light、1x/2x、reduced-motion 静态对照、GPU/内存预算，以及逐项资产来源和权利链。默认角色资产未完成权利审查时只能用于开发或受限测试，不能称为“合法可发布”。

### 13.4 主动行为预算

- 1.0 默认主动等级为 `quiet`；用户可选择 `off / quiet / balanced`，不提供无上限模式。
- quiet hours、macOS Focus/Do Not Disturb、会议/屏幕共享和全屏内容优先抑制非必要发言。
- 非用户请求的语音/气泡默认每小时最多 2 次、同一事件冷却 15 分钟；Watch/Scene 使用会话内独立设置。
- 每次主动表达提供静音/降低主动程度入口；重复事件去重，并能解释“为什么此刻发言”。

## 14. 非功能要求

| 类别 | 目标 |
|---|---|
| 启动 | Apple Silicon 干净机冷启动至可交互状态 p95 ≤ 10 秒 |
| 交互 | 本地 UI 状态更新 p95 ≤ 150ms；普通流式反馈首个可见事件 p95 ≤ 1.5 秒（不含供应商排队） |
| 稳定性 | Private Beta crash-free session ≥ 99.5% |
| 安全 | 发布门禁中未授权敏感动作 = 0；秘密/私有内容进入 UI/voice/release artifact = 0 |
| Computer Use | 限定真实任务集成功率 ≥ 90%，错误目标动作率 < 1%，敏感动作误自动执行 = 0 |
| 记忆 | 已保存事实正确召回率 ≥ 90%，明确删除后召回率 = 0 |
| 可恢复 | 暂停、审批等待和应用重启不得产生重复外部副作用 |
| 可访问性 | 核心 P0 旅程可用键盘和 VoiceOver 完成 |
| 资源 | 空闲时不得持续高占用摄像头/麦克风/屏幕捕获；后台观察必须有显式状态和预算 |
| 语言 | 1.0 launch locale 为简体中文，fallback locale 为英文；混合语言输入可用，缺 OCR/ASR/TTS 语言能力时明确降级 |

所有指标需要固定 Apple Silicon 设备档、macOS 版本、数据集、重复次数、统计窗口、p50/p95、失败样本审计和 owner，不使用主观“感觉良好”替代。

## 15. 成功指标

### 15.1 激活

- 已有受支持 provider 的新用户，从安装到首次成功 AI 对话的中位时间 ≤ 5 分钟；新配置 provider 与无 provider 用户分 cohort 报告。
- `ai_conversation_success` 必须来自可用 text route 的有效回答；规则设置引导只记为 `rule_guidance_completed`。
- 首次使用 Computer Use 时，用户能正确理解三种权限档位的比例 ≥ 90%。
- 权限拒绝后仍能完成至少一个非相关核心任务的比例 ≥ 95%。

### 15.2 价值

- 每周活跃用户中完成至少一次“观察—行动—验证”闭环的比例。
- 每个活跃项目的跨会话回访率和有用记忆召回率。
- Watch/Scene 中用户主动关闭“过度评论”的比例持续下降。

### 15.3 信任

- 审批取消率、接管率和策略阻止原因可分类解释。
- 用户报告的“Joi 声称完成但实际未完成”比例。
- 记忆删除、权限撤销和数据清空的成功率。

### 15.4 测量契约

- 激活：按版本、provider cohort 和首次安装日统计 7 日窗口，中位数至少 30 个有效样本；无产品遥测时使用明确 opt-in 研究或本地可导出计数。
- 观察—行动—验证：分母为发起受支持 Computer Use 任务的用户，完成要求存在 verified receipt。
- 记忆质量：使用版本化本地 fixture，分别报告正确召回、错误注入、跨项目泄漏、冲突和删除后召回。
- 审批取消率、接管率与策略阻止率是诊断指标，不直接判定为负向产品结果。
- Watch/Scene 的主动打断指标只在 Beta cohort 评估，不进入 1.0 Hero Journey 激活。

## 16. 发布范围

### 16.1 Private Alpha

- Apple Silicon macOS。
- 项目/对话、合法默认角色和 BYOK-first 基础 AI 对话。
- observe + step-by-step Computer Use。
- 主显示器与受支持多显示器范围的坐标验证；不可信范围 fail closed。
- 默认待确认记忆、删除控制、内建 Skills、审计和开发者诊断。

### 16.2 Private Beta

- collaborate 权限与项目绑定范围。
- Watch/Scene、可选 CUA。
- 第三方 Skill 安装/draft、完整角色 CRUD、Coding Agent 和游戏适配器 dry-run。
- 签名/公证 DMG 和干净机升级/迁移。

### 16.3 Public Candidate

- 法律、隐私、第三方资产和品牌门禁全部关闭。
- P0 需求、非功能目标和安全测试通过。
- 数据导出/删除、崩溃恢复、更新回滚和发布 provenance 可验证。

## 17. 主要风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Computer Use 误目标 | 真实外部损失 | 语义证据、歧义选择、敏感动作确认、后验验证、预算/暂停 |
| 角色表现掩盖系统状态 | 用户误信 | public phase 单一事实源、审批/失败不可被动效覆盖 |
| 记忆错误或越界 | 长期信任受损 | 候选治理、作用域、来源、编辑删除、召回评估 |
| 插件/Skill 供应链 | 主机执行风险 | inspect、哈希、脚本拒绝、独立进程、沙箱、签名规划 |
| macOS 权限与多显示器差异 | 核心能力不稳定 | 权限降级、真实设备矩阵、坐标回归、CUA/原生双路径 |
| 工作树现状与文档漂移 | 误判完成度 | TDD 现状/目标分离、自动契约测试、发布证据 |
| 角色资产许可 | 阻止公共发布 | 默认合法资产、来源清单、发布前权利审查 |
| 供应商不可用或成本变化 | 体验中断 | 稳定路由标签、BYOK、本地/规则降级、预算显示 |

## 18. PRD 验收标准

本文作为产品基线通过需满足：

- P0 需求均有 TDD 组件和验证映射；
- 现有能力与目标能力明确区分；
- 所有外部动作都有授权、暂停、验证和审计语义；
- 所有长期数据都有来源、作用域、查看和删除语义；
- macOS 权限缺失、模型缺失和资产加载失败有降级体验；
- 角色/美术要求不与安全、可访问性和性能冲突；
- 发布范围不依赖尚未解决的软件许可证和资产权利；
- Product、Technical、AI、Art、Trust & Safety、Quality & Release 六域完成收口评审。

## 19. 待决策项

以下事项不阻止 PRD/TDD 建立，但会阻止公共发布：

1. 最终软件许可证及第三方依赖/资产通知策略。
2. Joi 默认角色、应用图标、字体、声音和 Live2D/VRM 资产的最终权利。
3. 数据导出格式、备份加密与跨设备迁移范围。

已冻结的产品决策：

- 1.0 为 BYOK-first，不提供默认托管推理服务；本地兼容端点可选。
- 产品分析与崩溃上报默认关闭；任何未来遥测均需字段级审查和显式 opt-in。
- 1.0 支持 Apple Silicon macOS 12+；Intel Mac 与其他平台不作为发布承诺。

## 20. 术语表

| 术语 | 产品含义 |
|---|---|
| 项目 Project | 持久工作上下文、资源绑定和默认角色的容器 |
| 对话 Thread | 项目内连续交流记录；同一对话同时只有一个主动 run |
| Run | 一次用户目标的计划与执行生命周期；普通用户文案称“任务” |
| 能力会话 Capability Session | Computer Use、Scene 或 Game 的长生命周期控制边界 |
| Step | Run 中一个可审批、可执行、可验证的步骤 |
| Approval | 绑定具体 step、参数指纹、作用域和 TTL 的一次性用户决定 |
| ActionReceipt | 外部状态动作的前后证据、实际 driver 与验证结果 |
| Skill | 具有稳定 schema、权限、dry-run 和审计规则的能力扩展 |
| public phase | 面向角色与 Shell 的安全运行状态投影，仅在 Developer Mode 展示协议名 |

## 21. Joi Studio Scheme Review

2026-07-26 二轮方案评审：

| 负责人 | 决策 | 已收口范围 | 实现/发布仍需证据 |
|---|---|---|---|
| Product Design Director | `approved` | Primary persona、Hero Journey、macOS Presence、IA、优先级与 Beta 边界 | 可用性测试、指标样本和核心旅程真机结果 |
| AI & Companion Director | `approved` | BYOK-first、无模型降级、记忆治理、主动预算与质量门禁 | provider、记忆、语音和主动行为自动化评估 |
| Art Director | `approved` | 六 emotion、覆盖优先级、Art Bible 与资产交付清单 | 视觉样张、性能数据、默认资产权利证明 |

`approved` 表示产品方案可作为实施基线，不表示实现或公共发布已经完成。资产权利、签名公证、干净机、权限、多显示器和数据删除等证据继续由 TDD closeout 与 release gate 管理。

## 22. 参考资料

- [Project AIRI repository](https://github.com/moeru-ai/airi)
- [Project AIRI overview](https://airi.moeru.ai/docs/zh-Hans/docs/overview/)
- [Project AIRI Desktop / Tamagotchi manual](https://airi.moeru.ai/docs/en/docs/manual/tamagotchi/)
- [OpenHuman repository](https://github.com/tinyhumansai/openhuman)
- [OpenHuman getting started](https://github.com/tinyhumansai/openhuman/blob/main/gitbooks/overview/getting-started.md)
- [Letta repository](https://github.com/letta-ai/letta)
- [Letta Memory Blocks](https://docs.letta.com/v1-sdk/memory/memory-blocks)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangChain human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
- [OpenHands repository](https://github.com/OpenHands/OpenHands)
- [OpenHands runtime architecture](https://docs.openhands.dev/openhands/usage/architecture/runtime)
- [elizaOS repository](https://github.com/elizaOS/eliza)
- [Joi common-presence architecture](COMMON_PRESENCE_ARCHITECTURE.md)
- [Joi backend architecture](BACKEND_ARCHITECTURE.md)
- [Joi runtime architecture](../agent_companion/docs/architecture.md)
- [Joi macOS release guide](MACOS_RELEASE.md)
- [Joi privacy notice draft](PRIVACY.md)
