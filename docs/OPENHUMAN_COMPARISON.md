# Joi vs OpenHuman vs AIRI 综合对比 & 前端升级计划

## 三项目定位

| 维度 | Joi | OpenHuman (28k⭐) | AIRI (40k⭐) |
|------|-----|-------------------|-------------|
| 定位 | 角色人格多模态 Agent | 个人 AI 超级智能 | Neuro-sama 式 AI 伴侣 |
| 技术栈 | Python + Tauri/Vue | Node + Tauri/React | TypeScript + Vue/Electron |
| 核心差异 | Computer Use 审批链 | 记忆树 + 118 集成 | Live2D/VRM + 游戏 AI |

---

## OpenHuman 核心优势 (Joi 应吸收的)

### 1. 记忆树 (Memory Tree) — 最关键差距
OpenHuman 的记忆不是扁平存储，而是三级层次结构：
- **Source Tree**: 每个数据源一个滚动缓冲区 (L0 → L1 → L2 逐级密封)
- **Topic Tree**: 按实体(人/项目/话题)懒构建的热度摘要树
- **Global Tree**: 每日全局摘要

Joi 目前: 扁平 SQLite + FTS5 关键词搜索 (我们刚升级的)
差距: 没有层次摘要、没有实体热度、没有自动数据源拉取

### 2. 潜意识循环 (Subconscious Loop) — 体验差距
OpenHuman 有一个后台心跳系统：
```
每 N 分钟 → 加载待评估任务 → 构建情境报告 → 评估每个任务 → 执行/跳过/升级
```
即使用户停止输入，AI 仍在后台思考、整理记忆、评估任务。

Joi 目前: 纯被动响应，用户不说话就什么都不做
差距: 没有主动思考能力

### 3. 吉祥物情绪状态机 — 人格差距
OpenHuman 的吉祥物有 6 种状态：
- idle (空闲), thinking (思考), listening (倾听), talking (说话), surprised (惊讶), dreaming (发呆)
- 根据 Agent 行为自动切换
- 嘴型同步 (viseme map)
- 对话结果的情绪反馈 (成功→开心, 失败→担忧)

Joi 目前: 静态 sprite 表情，手动切换
差距: 没有自动状态机、没有口型同步

### 4. Token 压缩 (TokenJuice) — 成本差距
OpenHuman 的每条工具输出都经过压缩层：
- HTML → Markdown
- 长 URL 缩短
- 冗余输出去重摘要
- CJK/emoji 逐字保留
- 成本降低高达 80%

Joi 目前: 工具输出直接进入上下文
差距: 没有压缩层，token 浪费严重

### 5. 118+ 一键集成 — 生态差距
OpenHuman 通过 Composio 实现：
- Gmail, Notion, GitHub, Slack, Stripe, Calendar...
- 一键 OAuth，无需 API key
- 每 20 分钟自动拉取新数据到记忆树
- 作为 Agent 工具 + 记忆源 + 触发器

Joi 目前: 硬编码工具，无第三方集成
差距: 生态封闭

### 6. 会议参与者 — 场景差距
OpenHuman 的吉祥物可以：
- 以真实参与者身份加入 Google Meet
- 实时听取对话、记笔记
- 发言时驱动口型同步
- 将自己的动画形象作为摄像头画面

Joi 目前: 无会议集成

---

## Joi 自身优势 (要保留)

1. **Computer Use 审批链** — 操作前确认 + 前后截图验证 (比两者都强)
2. **语义目标三路融合** — OCR + Accessibility + Visual (OpenHuman/AIRI 没有)
3. **Watch Together** — 陪看模式 (视觉上下文 + 跟进问答)
4. **安全策略** — PolicyGate 风险分级 + 语音行过滤
5. **Tauri 原生性能** — 比 Electron 更轻量

---

## 前端升级计划

综合 OpenHuman 和 AIRI 的优势，针对 Joi 前端进行以下升级：

### F1. 吉祥物情绪状态机 (吸收 OpenHuman)
给 Joi 的角色添加自动情绪切换：
- 6 种状态: idle, thinking, listening, talking, surprised, dreaming
- 根据后端事件自动切换 (收到用户消息→listening, 工具执行中→thinking...)
- 状态转换动画 (CSS transition)

### F2. 潜意识指示器 (吸收 OpenHuman)
- 后台思考时显示微动画 (呼吸光效/旋转粒子)
- "正在整理记忆..." 状态提示
- 空闲时显示最近记忆摘要气泡

### F3. 任务进度可视化 (吸收 AIRI)
- 工作流步骤进度条
- 每步状态: pending → running → success/failed
- 可展开查看详情

### F4. Token 预算指示器 (吸收 OpenHuman)
- 显示当前对话 token 使用量
- 工具输出压缩率
- 成本估算

### F5. 集成状态面板 (吸收 OpenHuman)
- 已连接服务列表
- 每个服务的最后同步时间
- 一键连接新服务 (预留)
