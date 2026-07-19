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

## 能力会话与权限

每次 Computer Use、Scene Session 或 GameAdapter 运行均建立 `CapabilitySession`。会话记录目标、权限档位、驱动、预算、停止条件及状态。

权限语义：

- `observe`：只读。
- `collaborate`：只在项目绑定范围内自动执行。
- `delegate`：当前 Joi 启动会话内持续执行。

付款、登录授权、对外发消息、删除、安装和扩权不会因档位升级而跳过确认。暂停、继续、取消与用户接管都通过同一状态机处理，接管不会删除目标或动作回执。

## Computer Use

感知顺序固定为应用 API/DOM、Accessibility、OCR/视觉、坐标兜底。动作改变外部状态后必须再次观察，并生成 `ActionReceipt`。连续无变化、观察签名循环或焦点漂移会暂停会话；步骤、时间、模型调用和失败次数均有预算。

Joi 原生 macOS 驱动始终可用。可选 CUA 驱动使用 `cua-driver` 的窗口级截图与后台输入：

1. 只有 `cua-driver status` 成功时才会被自动选择。
2. `get_window_state` 的窗口局部截图直接作为规划坐标空间。
3. 点击、输入、快捷键、滚动和拖拽携带 pid/window_id，并使用 `delivery_mode=background`。
4. 驱动缺失、daemon 未运行或初始化失败时回退原生驱动，不会伪装成后台执行。

## Skill 平台

`AgentSkillService` 支持本地目录、ZIP 和 Git 来源，识别 `SKILL.md`、`scripts/`、`references/` 与 `assets/`。安装预览展示来源、版本、许可证、哈希、依赖、脚本和权限。全局、角色、项目作用域按“项目 > 角色 > 全局”解析。

ZIP 路径穿越、符号链接、未知脚本和安装后哈希变化会被拒绝。代码型 Skill 默认禁用隐式运行，并且只有非观察会话加显式首次确认才能执行。脚本从不导入 Joi 主进程；macOS 使用独立进程和 Seatbelt 配置，默认禁止网络，仅允许声明的项目与运行目录写入。

成功操作只能生成 Skill 草稿，审核输入、步骤与权限后才可以安装。

## Scene Session

陪看默认为“安静共看”。Scene Session 比较画面、字幕、转写与章节变化，只在模式和事件显著度允许时评论。支持解说、翻译、分析、无障碍描述、剧透等级与语音打断。

默认不保留原始视频和音频；会话只保存用户允许的摘要、书签和记忆候选。截图仍受现有临时视觉工件清理策略管理，不作为长期媒体库存档。

## GameAdapter

GameAdapter manifest 声明检测、观察源、动作集、暂停、验证、存档点和平台。安装、启停与卸载状态独立于角色包。

- OK-WW 已包装为经过审查的可安装适配器，保留 dry-run 和显式授权。
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
