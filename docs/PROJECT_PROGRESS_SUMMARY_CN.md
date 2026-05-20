# Joi 项目进度总结（给人看的版本）

这份文档是为了避免我们只靠聊天上下文记忆项目进度。以后如果 Codex 记忆压缩、换线程、换机器，先看这份和 `docs/AGENT_PROGRESS_MEMORY.md`。

## 一句话现状

Joi 现在已经不是早期原型了。它正在进入 **MVP 前的安全打磨阶段**：核心方向是让一个角色化的桌面 agent 能看屏幕、理解界面、提出可点击目标、经过你确认后执行电脑操作，并且能把每一步审计清楚，不乱点、不泄露隐私、不把技术细节读出来。

## 我们这段时间主要完成了什么

### 1. 把项目主线从原型清理成 Joi

前面先做了很多“打地基”的事：清理旧 prototype、第三方角色资产、本地配置，把代码收束到现在的 Joi 主线：

- Python Core 在 `agent_companion/core/`
- Tauri/Vue Shell 在 `agent_companion/shell/`
- 文档、roadmap、known issues 在 `docs/`

这一步很重要，因为它让 Joi 从“实验堆叠”变成一个可以继续长期开发的项目。

### 2. 视觉观察和 Watch Together

Joi 现在可以走 Windows 截屏链路，观察 active window/fullscreen，并通过 vision/OCR 得到屏幕内容。Watch Together 不只是看一张图，而是能保留当前会话里的最近观察结果，用来回答“你刚才看到了什么”“这个页面有什么按钮”这类追问。

目前 Watch 记忆还是 session-only，没有做长期持久化，这是刻意保守的。

### 3. 语音输入输出安全化

我们做了语音输入基础和生产 ASR 路由：

- shell 里点击录音
- Core 通过 `voice.transcribe` / `audio.transcribe` 走正常用户消息路径
- 支持 OpenAI-compatible ASR
- 音频只在内存里处理
- 有大小限制、超时、错误提示
- 新指令会打断旧语音
- late TTS 不会在新意图之后突然冒出来

这块的重点不是“能说话”这么简单，而是避免旧任务的语音在错误时间播放，避免语音路径绕过审批。

### 4. Computer Use 从能点变成可审计、可验证

Computer Use 现在有比较完整的安全链路：

- observe / click / type / scroll / hotkey
- 所有电脑操作都需要中风险审批
- approval id 是一次性的，并绑定 task、step、tool、arguments hash
- 操作后会重新观察屏幕
- 会用 OCR/title/dimension 和本地图像 diff 判断是否真的发生变化
- developer mode 里有审计 timeline

换句话说，它不是“模型说点哪里就点哪里”，而是：先观察、提出动作、你确认、执行、再验证、再审计。

### 5. 语义目标定位逐步硬化

这是最近 P4.x 最大的一条主线。目标是让 Joi 能听懂“点登录”“点右下角开始”“选第二个目标”这种自然语言，然后在屏幕上找候选目标。

现在已经有：

- OCR 文本区域
- Windows UI Automation 控件树
- visual detector fallback
- 候选排序、置信度、歧义判断
- 多候选 selection flow
- `selection_id` 绑定，防止旧卡片误选新上下文
- screenshot bbox 到 screen coordinate 的 capture rect 转换
- candidate preview overlay

更重要的是，我们不断加了很多“不要乱点”的安全夹层：

- static UIA text 不能直接点击
- disabled UIA 控件不能审批点击
- visual-only 候选必须先让用户选择，不能自动点
- 坐标转换不可信就 fail closed
- UIA/screen bounds 必须落在 trusted capture rect 内

### 6. 从 4.8.2 到现在的 review 线

我们确实是从 `review4.8.2` 开始持续做 review 的。因为聊天上下文被压缩，之前每个版本的完整细节不一定都还在当前线程里，所以这份文档把可从 git/docs 还原的主线固化下来。

大致路线是：

- 4.8.2 到 4.18：Computer Use、Watch、Voice、OCR、审计、语义目标定位的基础和安全硬化。
- 4.19：safe runtime config writer foundation。
- 4.20：runtime settings approval flow，但 review 找到 apply RPC 合约问题。
- 4.21：修复 runtime apply precheck，通过。
- 4.22：dense semantic calibration，semantic suite 到 18。
- 4.23：modal/capture-scale calibration，semantic suite 到 24。
- 4.24：multi-window/clipped-capture calibration，semantic suite 到 30，但 review 找到 P2：UIA/screen_bbox 中心点可能跑出 trusted capture rect。
- 4.25：修复 UIA capture rect click trust，semantic suite 到 33，通过。
- 4.26：加入 multi-monitor/mixed-scale calibration，semantic suite 到 38，但还需要正式 review。

### 7. 最近这两天最大的技术点

最近最关键的是 4.24 到 4.25。

4.24 加了很多窗口裁剪、多窗口、partial capture 的 fixture，但 review 时发现一个真实安全漏洞：UIA bounds 只要和截图有一点重叠，就可能通过 preview 检查，但它原始 screen center 可能在可信截图外面。这样有可能让 Joi 点到不可见、不可信的区域。

4.25 把这个洞补上了：现在 UIA/screen_bbox direct approval 和 selected candidate continuation 都必须确认原始 screen center 还在 trusted capture rect 内，否则就 fail closed。

这是 MVP 安全性里非常核心的一类 bug。

### 8. 4.26 已进入主线，但还没正式 review

GitHub main 上现在已经有：

`c176c9a Add multi-monitor semantic calibration fixtures`

文档显示 4.26 把 semantic grounding suite 扩到 38 cases，覆盖：

- negative-origin active-window offset
- moved-window stale bounds
- mixed Retina/non-Retina scale
- overlapping same-label windows
- dense repeated actionable labels

但它还没按我们的 review 流程正式 review，所以明天第一件主线任务应该是：

```text
review4.26 并给出下一步
```

### 9. Autopilot 方向

我们今天也探索了自动化开发/runtime。

先看了 `loop`、OpenAI Agents SDK + Codex MCP、OpenHands/OpenClaw/LangGraph 等方案。结论是：

- `loop` 很像现成 worker/reviewer pair，但默认太猛，会 yolo/dangerously skip permissions，不适合直接裸跑 Joi。
- OpenAI Agents SDK + Codex MCP 是最完整的原生多 agent workflow，但需要 API key，而 API key 会按 OpenAI Platform 计费，不等于你已经付费的 Pro/Codex subscription。
- 所以我们改成 Codex-only autopilot，默认复用本机 Codex CLI 的 ChatGPT 登录态，不需要 `OPENAI_API_KEY`。

现在已经提交：

`ddb624b Add Codex-only autopilot skeleton`

它包括：

- `tools/joi_autopilot.py`
- `docs/REVIEW_HANDOFF.md`
- `docs/AUTOPILOT_LOG.md`
- `docs/AUTOPILOT_NATIVE_WORKFLOW.md`
- `docs/AUTOPILOT_RUNTIME_SPIKE.md`

这个 autopilot 现在只是骨架，还没有证明夜间全自动开发可用。明天在 Windows 上应该先跑 preflight，不要直接跑长任务。

## 现在离完整 MVP 还有多远

我的判断：Joi 现在是 **MVP Core 后段 / 正式 MVP 前安全打磨期**。

已经比较扎实的部分：

- Core/Shell 基本结构
- Watch/vision/OCR
- Voice input 基础
- Computer Use 审批链路
- post-action verification
- developer audit timeline
- semantic target selection
- runtime provider status
- safe runtime settings subset

还需要继续补的部分：

- 4.26 正式 review
- 更多真实 Windows dense layout 校准
- 真实用户环境里的 OCR/Tesseract/ASR 配置体验
- local real screenshot calibration 继续转 synthetic fixtures
- OK-WW/game skill 更完整的 completion 状态读取
- 角色表现和 Live2D/VRM/表情还不是产品级
- full provider/secret settings UI 还没做
- autopilot 只到 skeleton，未验证夜间开发闭环

如果只说“小范围可演示 MVP”，已经比较接近。
如果说“正式稳定完整 MVP”，还需要继续打磨几周，尤其是 Windows 真实环境和 Computer Use 安全边界。

## 明天建议顺序

1. 在 Windows 上拉最新 main。
2. 跑 Joi 原有测试和 shell build。
3. 跑 `tools/joi_autopilot.py --preflight`，只验证 autopilot 环境，不先让它自动开发。
4. 做 `review4.26 并给出下一步`。
5. 如果 4.26 过，再继续 P4.27：更大真实布局/游戏 HUD/窗口焦点切换校准。

## 给未来 agent 的提醒

不要只记得 autopilot。Joi 当前真正的主线仍然是：

```text
Computer Use / Watch grounding / semantic target safety / auditability
```

Autopilot 是辅助开发系统，不是 Joi 产品本体的替代目标。
