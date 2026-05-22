# Joi 智能体伴侣：架构优化与多模态设计建议书

**Date**: 2026-05-22  
**Target Audience**: Windows-side Codex / Developer Agents & Joi Core Developers  
**Purpose**: 提供针对 Joi 项目（基于 OpenHuman 启发）在架构收口及后续 P5-P9 阶段的深度工程优化建议，促进双端（Mac 协同端与 Windows 主力执行端）高效协同。

---

## 1. 核心评估与基本盘共识

Joi 成功避开了“万物集成但臃肿无感”的 AI 操作系统陷阱，其**“具身化智能伴侣（Embodied Agent Companion）”**的差异化定位非常清晰。

当前在 **P1-P4 阶段**，Joi 展现出以下优秀设计纪律：
- **安全沙箱与一事一议的审批链**（使用 single-use `approval_id` 进行 Arguments Hash 绑定）。
- **非机器播报原则（No-Machine-Speech）**，确保 `voice_line` 极致干净。
- **高精度的视觉/UIA/OCR 融合语义定位**，处理好了 Retina/Fractional 缩放兼容。

为了让后续的 `P5 Memory Core`、`P6 JoiJuice` 和 `P7 Model Router` 能以极高的代码质量落地，建议 Windows 端 Codex 重点采纳以下四项架构优化规范。

---

## 2. 优化建议与工程落地指南

### 建议一：将 P6 JoiJuice 结果解耦层提速前置 (Core Input Sanitization)

> [!WARNING]
> **现状风险**：当前随着 Vision Layer 和 Computer Use 逻辑越来越重，原始 of OCR 段落、UIA 属性树和 coordinates 坐标数据极为庞大。如果直接丢给 LLM，会导致 Context 瞬间暴涨，且极易在 TTS 播报中发生“代码/路径泄漏”。

#### 💡 解决方案：强制工具返回管道进行五分流（JoiJuice Schema）
每一个核心工具（如 `Codex`、`Browser`、`Computer Use`、`Game Skill`）执行完毕后，其输出必须经过统一的 `JoiJuice` 压缩器，规范化输出为以下结构：

```json
{
  "agent_state": {
    "desc": "专门喂给 LLM Planner 的极简状态描述，剥离所有像素坐标和临时乱码。",
    "status": "success",
    "changed": true
  },
  "display_card": {
    "desc": "专门喂给 Tauri 前端渲染精美任务卡（Task Card）的数据，可包含富文本、图片引用、差异对比图等。",
    "title": "网页陪看",
    "summary": "分析了当前页面，检测到 5 个按钮",
    "artifacts": ["data/screenshots/after_click.png"]
  },
  "voice_line": {
    "desc": "绝对安全的自然短句，供 TTS 合成，严禁夹带绝对路径、JSON、ID、坐标等。",
    "text": "我已经帮你点按了右上角的保存按钮，页面已成功更新。"
  },
  "memory_candidate": {
    "desc": "可选。过滤后的人机可读倾向性陈述，留待 P5 存储。",
    "fact": "用户在编写 CSS 时更倾向于使用原生 CSS 变量而非第三方框架。"
  },
  "audit_log": {
    "desc": "详细的本地调试日志，包含所有 raw traceback、绝对路径、句柄 ID。只记录在本地，不上传给模型，也不读出来。",
    "trace_id": "audit-task-42"
  }
}
```

---

### 建议二：P5 Memory 引入“记忆气泡授权”与“本地 Markdown 审计” (User-in-the-Loop Memory)

> [!IMPORTANT]
> **隐私共识**：作为“值得信赖的伴侣”，Joi 的记忆不能像黑盒一样静默写入，必须要给用户绝对的安全感与掌控感。

#### 💡 解决方案：记忆注入机制
1. **生成阶段**：工具在运行中触发 `memory_candidate`，比如 Codex 发现用户在 `secrets.yaml` 中配置了特定的开发路径。
2. **拦截阶段**：在 Core 中通过安全过滤器（Privacy Gate），判定如果是含有路径、疑似 token 或敏感词的 candidate，直接就地销毁（Fail-Closed）。
3. **授权阶段 (UI Bubble)**：通过安全策略后，前端 Joi 舞台的头像上方弹出一个精美的微缩气泡：
   > 💬 *Joi 记住了：“您目前在开发 Joi 项目，且习惯用 Microsoft YaHei 字体”。*
   > 点击该气泡可直接打开 `Local Memory Editor`。
4. **存储形式**：
   - 数据库侧：使用本地 SQLite 存储关系权重与 persona notes。
   - 人类侧：在本地用户目录（如 `data/memory/`）维护一个人类完全可读、可手动任意修改的 `joi_memory_vault.md`。Joi 每次读取记忆时，以这个 Markdown 文件为最高优先级准则。

---

### 建议三：跨平台开发舱抽象隔离 (Cross-Platform Isolation)

> [!NOTE]
> **研发痛点**：目前大部分 API（如 Windows Active Window、UIA tree 捕获、Clipboard Paste）均是 Windows 平台特异（Windows-First）的代码，这导致在 Mac 上进行协同前端/逻辑开发时，经常会因为导入 `ctypes` 或 Windows 特定 DLL 发生 Crash。

#### 💡 解决方案：引入跨平台策略适配器模式 (OS Adapter Pattern)
核心操控逻辑必须继承自统一的 Base 类。在底层初始化时，利用平台探测机制自动决定是装载真 Windows 驱动，还是装载跨平台仿真 Mock 驱动：

```python
# agent_companion/core/computer_use/platform.py
import platform

class BaseComputerAdapter:
    def click(self, x: int, y: int) -> bool:
        raise NotImplementedError()

class WindowsComputerAdapter(BaseComputerAdapter):
    def click(self, x: int, y: int) -> bool:
        # 调用 Windows ctypes 物理模拟鼠标点击
        pass

class MockComputerAdapter(BaseComputerAdapter):
    def click(self, x: int, y: int) -> bool:
        # Mac/Linux 协同环境下的降级处理：仅在日志中输出模拟点击，不崩程序
        print(f"[Mock Click] coordinates: ({x}, {y})")
        return True

def get_computer_adapter() -> BaseComputerAdapter:
    if platform.system() == "Windows":
        return WindowsComputerAdapter()
    return MockComputerAdapter()
```
这样做能保证不论在 Windows 真机还是 Mac 模拟开发机上，回归测试（`run_agent_companion_tests.py`）都能 100% 编译和通过！

---

## 建议四：多模态情绪表达（Expression Sync）管道

> [!TIP]
> **提升人设品质**：一个生动的伴侣需要声、图、表情多模态的高度同步。现有的路由只是把 Text、Vision、Expression 拆开，缺乏一个横向的情绪纽带。

#### 💡 解决方案：情绪同步管道的设计
1. **意图阶段**：在 `Model Router` 中开辟 `voice_style` 路由。大模型生成回答的同时，附带输出一个极简的情绪情感 Token，例如 `<emo: happy>`、`<emo: thinking>`、`<emo: alert>`。
2. **Core 处理阶段**：Core 剥离该 Token，并同步分发给两个端：
   - **TTS 合成端**：将 `voice_line` 发送给 TTS 服务（如 GPT-SoVITS），如果支持语气，动态添加情绪调节参数。
   - **Vue 前端展示端**：向 Tauri 发送包含表情指示符的事件（例如 `activeSpriteId = "happy"`）。
3. **前端渲染阶段**：Vue 端接收后，立绘直接切换为相应的喜悦表情，并配合微动效进行反馈，实现真正的多模态共鸣。

---

## 3. Windows 端 Codex 推荐的首要任务

在进入 P5 阶段前，建议 Windows 端 Codex 优先完成以下重构，以为本项目彻底扫清障碍：
1. **重构 `agent_companion/core/policy.py`**：将目前的 Computer Use 结果统一封装为 `JoiJuice` 标准的五分流。
2. **在 `tests/` 中编写 `test_voice_leaks.py`**：利用正则库拦截测试，验证所有核心工具的 `voice_line` 在面对特殊敏感数据（路径、Token、坐标）时是否能做到 100% 自动隐匿。
