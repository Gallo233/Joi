# Joi 后端架构

## 目标

Joi 是一个本地优先的 AI companion 与通用 Agent 宿主。后端需要同时支持角色对话、长期记忆、工具执行、Computer Use、实时陪看、语音和外部 Agent CLI，但这些能力不能继续依赖单个“大而全”的 Bridge 或 App 类。

这次重构采用以下约束：

- 前端继续使用现有 WebSocket JSON-RPC 方法，不做破坏式协议变更。
- 角色体验属于表现层，Agent runtime 不依赖 Vue、Tauri 或窗口状态。
- 工具、记忆、语音和平台能力通过明确边界接入，而不是在传输层直接拼业务逻辑。
- 审批必须绑定稳定的动作指纹；失败、过期和拒绝都默认不执行。
- 本地单进程仍是默认部署方式，但协议和运行时可以独立测试，后续可接持久化任务队列。

## 分层

```text
Tauri / Codex / MCP clients
             │
             ▼
Transport adapters
  server.py / joi_mcp_server.py
             │
             ▼
Protocol + command routing
  core/rpc/
             │
             ▼
Application services
  core/services/
             │
             ▼
Agent runtime
  app.py / core/runtime/
             │
       ┌─────┴────────┐
       ▼              ▼
 Domain stores     Tool adapters
 memory, audit,    tools/, vision/,
 background       computer_use/
```

### Transport adapters

`server.py` 只负责 WebSocket 生命周期、事件广播、语音数据传输和把请求交给路由。RPC 方法以声明式注册，不再由超长 `if/elif` 分支承担协议、调度和业务三种职责。

`joi_mcp_server.py` 是 MCP 到 Joi Core 的适配器。公开的 `TOOL_SCHEMAS` 与工具路由一一对应，新增工具时可以检测“发布了 schema 却没有 handler”的错误。

匿名网站体验增加了一个 loopback session broker，但没有把 Core 改成多租户：
每个访客仍独占一个 Core 和 workspace。broker 解析动态 WS/资源路由，Caddy 只做
TLS；Core 在认证后、dispatch 前执行公开 RPC 白名单。隔离、Origin、预算与资源
URL 契约详见 [WEB_EXPERIENCE_ARCHITECTURE.md](WEB_EXPERIENCE_ARCHITECTURE.md)。

### Protocol

`core/rpc/protocol.py` 是 JSON-RPC 请求与响应的唯一编码边界；`core/rpc/router.py` 统一同步、异步和线程执行，并显式标记会改变 `core.ready` 的命令。

### Application services

`core/services/memory.py` 提供长期记忆命令边界，限制召回数量并统一 ID 校验；`core/services/background.py` 统一背景上下文变更及审计；`core/services/artifacts.py` 负责图片制品读取、工作区越界防护、类型和大小限制。Transport 不再直接操作 SQLite store 或文件路径。

### Agent runtime

`AgentCompanionApp` 保留会话级运行状态和事件语义。计划启动、工具执行、动态审批、结果处理和终态发射已拆为小型阶段；工具构造迁移到 `core/runtime/tool_factory.py`，避免 runtime 同时承担基础设施装配。

### 安全与审批

Codex CLI 定位与审批指纹集中在 `core/codex_support.py`。审批指纹使用规范化 JSON 的 SHA-256 截断值，不再依赖进程随机化的 Python `hash()`。审批拒绝、过期和动作不匹配共用同一 fail-closed 流程。

## 参考项目与取舍

- [Airi](https://github.com/moeru-ai/airi) 将纯 Agent runtime 从 UI 宿主中抽离，并通过 session、context、stream、LLM ports 与平台适配器连接。Joi 采用相同的“runtime 不依赖 transport”方向，但保留 Python 本地宿主。
- [elizaOS](https://github.com/elizaos/eliza) 将 core runtime、agent loader、app API 与 actions/providers/services 插件拆开。Joi 的工具 registry 与 application services 对应这一插件和服务边界。
- [LangGraph Agent Server](https://langchain-ai.github.io/langgraph/concepts/langgraph_server/) 把 API、任务执行、持久化和流式事件作为可独立扩展的组件；其 [persistence](https://langchain-ai.github.io/langgraph/concepts/time-travel/) 与 [interrupts](https://langchain-ai.github.io/langgraph/concepts/breakpoints/) 模型是 Joi 下一阶段持久化 run/checkpoint 和可恢复审批的参考。

“Luna”没有唯一、可确认的开源后端仓库，因此不把未经验证的实现细节当作架构依据；其简洁角色体验只作为前端产品方向。

## 迁移规则

1. 旧 RPC 名称和返回对象保持兼容，先增加路由/服务测试，再移动实现。
2. transport 不能新增直接数据库访问；文件读取必须经过 service。
3. runtime 新能力优先作为工具 adapter 或 service 接入。
4. 工具 schema、handler 和权限策略必须同时存在。
5. 所有审批数据使用稳定指纹，恢复时重新校验工具名、参数和 TTL。

## 后续阶段

- 将运行中的 plan、step、approval 变成可持久化 run/checkpoint，使应用重启后能安全恢复或明确取消。
- 为 memory、watch、voice 定义更窄的 ports，减少 `AgentCompanionApp` 对具体 store/provider 的了解。
- 把 `joi_mcp_server.py` 的 Computer Use 结果摘要和浏览器状态采集继续拆成独立 adapter。
- 将当前单进程串行锁升级为按 conversation/run 隔离的执行队列；同一会话保持单 run，不同会话可并发。
- 用协议契约测试替代 `run_agent_companion_tests.py` 中逐步累积的源码字符串断言。
