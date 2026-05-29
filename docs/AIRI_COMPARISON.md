# Joi vs AIRI 对比分析 & 优化计划

## 项目概况

| 维度 | AIRI (moeru-ai/airi) | Joi |
|------|---------------------|-----|
| Stars | 40,000+ | - |
| 技术栈 | TypeScript, Vue 3, Electron, pnpm monorepo | Python, Tauri, Vue 3 |
| 架构 | 57 个包的微服务 monorepo | 单体 Python + Tauri shell |
| 平台 | Web / macOS / Windows / Linux / iOS | Windows 优先, macOS 移植中 |
| 定位 | Neuro-sama 式 AI 伴侣/VTuber | 角色人格包裹的多模态 Agent 伴侣 |

---

## AIRI 核心优势 (Joi 应吸收的)

### 1. 插件协议 (Plugin Protocol)
AIRI 有 `@proj-airi/plugin-protocol` — 定义了 WebSocket 事件契约、插件与宿主的通信协议。
Joi 目前是硬编码工具注册 (`ToolRegistry`)，无法动态扩展。

### 2. 工作流引擎 (Workflow Engine)
AIRI 的 `computer-use-mcp` 有完整的工作流系统：
- `createAppBrowseAndActWorkflow` — 浏览+操作组合工作流
- `createDevInspectFailureWorkflow` — 开发失败检查工作流
- `createDevRunTestsWorkflow` — 测试运行工作流
- 工作流可暂停/恢复 (`resumeWorkflow`)
- 策略评估 + 恢复计划 (`evaluateStrategy`, `buildRecoveryPlan`)
Joi 目前只有简单的线性 step plan，无法处理复杂多步任务。

### 3. MCP 标准协议
AIRI 使用 Model Context Protocol 作为工具通信标准。
Joi 有 `McpListTool` 但没有真正集成 MCP 作为工具层。

### 4. 语义记忆 (Semantic Memory)
AIRI 使用 pgvector 做向量语义记忆，支持语义搜索。
Joi 只有基础 SQLite 记忆，没有向量检索。

### 5. 多游戏服务
AIRI 有独立的 Minecraft 服务 (Mineflayer + 认知栈)，Factorio 支持。
Joi 只有 OK-WW 单游戏适配器。

### 6. 浏览器 DOM 控制
AIRI 区分了 `browser_dom_*` (DOM 操作) 和桌面鼠标点击。
Joi 的 browser 工具只有搜索和观察，没有 DOM 操作能力。

### 7. 角色渲染
AIRI 支持 Live2D + VRM + Spine，有自动眨眼/注视/口型同步。
Joi 的角色阶段还是静态 sprite。

### 8. 多平台聊天
AIRI 集成 Discord (语音频道)、Telegram、Twitter。
Joi 没有外部聊天平台集成。

### 9. 模型提供商
AIRI 通过 xsai 支持 30+ 提供商 (OpenRouter, vLLM, Ollama...)。
Joi 的 Model Router 只支持有限的几个。

---

## Joi 自身优势 (应保留的)

1. **Computer Use 审批链** — 完整的 approval_id + 一次性确认 + 审计时间线
2. **语义目标定位** — OCR + Accessibility + Visual Detector 三路融合
3. **前后截图验证** — 操作前/后截图对比 + image-diff 验证
4. **角色人格系统** — CharacterHarness + ExpressionEngine
5. **Watch Together** — 陪看模式 (视觉摘要 + OCR 上下文 + 跟进问答)
6. **安全策略** — PolicyGate 风险分级 + 语音行过滤敏感信息
7. **开发者模式** — 运行时状态检查 + 审计时间线可视化

---

## 优化计划 (按优先级排序)

### Phase A: 架构基础 (1-2 周)

#### A1. 插件化工具注册
- 目标: 工具不再硬编码在 app.py，支持动态发现和注册
- 做法: 定义 `ToolPlugin` 协议 (name, version, capabilities, setup)
- 收益: 新工具只需放在目录下即可被发现

#### A2. MCP 工具层集成
- 目标: Joi 的工具既可以是内部 ToolAdapter，也可以是外部 MCP server
- 做法: 在 ToolRegistry 上加 MCP adapter，将 MCP tools 映射为 ToolRequest
- 收益: 可以复用 AIRI 生态的 MCP 工具

### Phase B: 智能升级 (2-3 周)

#### B1. 工作流引擎
- 目标: 支持条件分支、循环、暂停/恢复的复杂任务
- 做法: 定义 `WorkflowStep` (action | condition | loop | parallel)
- 优先实现: 浏览+操作工作流、测试运行工作流
- 收益: "帮我在这个网页上填写表单并提交" 这类复合任务

#### B2. 语义记忆
- 目标: 用向量搜索替代关键词匹配的记忆检索
- 做法: SQLite + 简单向量索引 (先不引入 pgvector 重依赖)
- 收益: "上次我们聊的那个项目" 能被正确召回

#### B3. 模型提供商扩展
- 目标: 支持 OpenRouter, Ollama, vLLM 等本地/代理提供商
- 做法: 统一 OpenAI-compatible 接口，配置文件声明 provider
- 收益: 可以用任何本地模型

### Phase C: 体验增强 (3-4 周)

#### C1. 浏览器 DOM 控制
- 目标: 除了截图观察，还能直接操作网页 DOM
- 做法: 通过 Tauri webview 或 Playwright 注入 JS
- 收益: "帮我在这个页面点击按钮" 不需要坐标定位

#### C2. 角色渲染升级
- 目标: Live2D 动态表情替代静态 sprite
- 做法: 集成 pixi-live2d-display 或类似库
- 收益: 角色有呼吸、眨眼、口型同步

#### C3. 外部聊天平台
- 目标: Joi 可以通过 Telegram/Discord 与用户交互
- 做法: 复用 AIRI 的 bot adapter 模式
- 收益: 不在电脑前也能和 Joi 对话

---

## 立即可做的优化 (本次实施)

从 Phase A 开始，先做 A1 (插件化工具注册) 和 A2 (MCP 集成基础)。

---

## 实施状态

### Phase A: 架构基础 ✅
- [x] A1. 插件化工具注册 — `plugin_protocol.py` + `registry.py` 升级 + 示例插件
- [x] A2. MCP 工具适配器 — `mcp_adapter.py` (stdio + HTTP transport)

### Phase B: 智能升级 ✅
- [x] B1. 工作流引擎 — `workflow.py` (条件分支/暂停恢复/重试/模板变量)
- [x] B2. 语义记忆 — `memory.py` 增强 (FTS5 全文搜索 + 重要度 + 标签)
- [x] B3. 模型路由 — `model_router.py` (多路由/fallback/延迟统计)

### Phase C: 体验增强 (待定)
- [ ] C1. 浏览器 DOM 控制
- [ ] C2. 角色渲染升级 (Live2D)
- [ ] C3. 外部聊天平台 (Telegram/Discord)
