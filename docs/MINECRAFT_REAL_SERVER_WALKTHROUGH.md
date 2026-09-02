# Minecraft 真实服走查

> 状态：验收清单。`docs/MINECRAFT_CLOSED_LOOP_SLICE.md` §1 定义的切片验收就是「一条真实 HMCL/LAN 世界走查」，这份文档把它拆成可勾选的场景。
> 关联：`docs/MINECRAFT_CLOSED_LOOP_SLICE.md`、`docs/REALTIME_VOICE_DEBUG.md`、`docs/KNOWN_ISSUES.md`。

## 为什么需要它

离线烟测（`tools/minecraft_p5_smoke.py`）在确定性 fake 世界里跑通桥接执行的全部 22 个动作、方块预算上限和 no-replay 恢复路径，但 fake 世界没有寻路、没有敌怪、没有真实延迟，也没有别的玩家。**只有最初的 10 个原语在真实世界里跑过**；战斗、屏幕证据、自主循环、计划、游戏内聊天和后来新增的 12 个动作都还没有。

这条走查是唯一能证明切片闭环成立的证据。

## 先决条件

- HMCL 启动一个 Java Edition 单人世界，「对局域网开放」，把屏幕显示的临时端口填进 Joi 的 Minecraft Skill。
- 世界里准备好：几棵树、石头、一个箱子、一张工作台、一座熔炉、一张床、一片水域。
- 生存模式，夜间可刷怪（战斗场景需要）。
- 角色包设定为「胆小」类人设（验证方案 A 的人格反应）。
- 若要走查屏幕证据场景，macOS 需已授予屏幕录制权限。

先跑一遍离线门禁，再上真实服：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' && .venv/bin/python tools/minecraft_p5_smoke.py
```

## 隐私边界

走查跑在用户自己的世界里。记录只能留下场景名、结论、类别和一句短话。

**不得记录**：坐标、服务器地址与端口、世界名/存档名、玩家名、session/goal/approval/receipt id、截图、日志原文、路径。

记录工具会拒绝看起来像坐标或地址的备注：

```bash
.venv/bin/python tools/minecraft_walkthrough_report.py --init
```

```bash
.venv/bin/python tools/minecraft_walkthrough_report.py --add combat_persona --status pass --category ok --note "怕的时候先跑，等指令才动手"
```

```bash
.venv/bin/python tools/minecraft_walkthrough_report.py --summary
```

结果写进被忽略的本地文件 `data/local_visual_eval/minecraft_walkthrough.local.md`，不进仓库。

## 场景清单

每个场景勾完后用上面的命令记一条。失败时不要继续往下跑依赖它的场景，先记 `fail` 和类别。

### 1. `join` 连接与进服

- [ ] 填入 LAN 端口，Skill 面板显示连接配置
- [ ] 精确确认 scope：server/world/dimension/半径/玩家/方块/建造与容器权限/动作与方块预算
- [ ] digest 审批卡片出现，确认后建立 persistent session
- [ ] Joi 出现在世界里；面板显示已连接
- [ ] 审批文案里写明自主行为与用户共享同一份额度

失败类别参考：`scope_wrong`、`runtime_error`。

### 2. `observe` 状态与环境观察

- [ ] `observe`：回执有维度、血量、饥饿、背包
- [ ] 回执里有 `world`（时间/天气/附近实体普查）与 `nearby_blocks`（名称+数量+方位+距离档）
- [ ] `inventory`：物品名与数量可读
- [ ] **回执、字幕、语音里都没有任何坐标**

失败类别：`coordinate_leak`、`receipt_unclear`。

### 3. `queries` 只读查询

- [ ] `lookup_recipe`：问一个没做过的东西，答出所需材料与是否需要工作台
- [ ] `inspect_container`：读箱子内容而不清空它
- [ ] `locate`：答出方向与距离档（如「north 方向，far」），**不是坐标**
- [ ] 三个查询都进回执，都各扣一次动作预算

失败类别：`coordinate_leak`、`action_unverified`。

### 4. `movement` 跟随与靠近

- [ ] 说「跟着我」，Joi 跟上（不需要报玩家名——Core 会填）
- [ ] 走远后说「过来」，Joi 靠近
- [ ] 用大小写不同的玩家名再试一次，仍然被允许
- [ ] scope 外的玩家名被拒绝

失败类别：`scope_wrong`、`action_refused`。

### 5. `gather` 采集与挖掘

- [ ] `collect` 橡木：物品真进背包才算完成
- [ ] `mine` 石头：回执的 `blocks_changed` 与实际相符
- [ ] 采不到时报的是具体原因，不是一句失败

失败类别：`action_unverified`、`receipt_unclear`。

### 6. `craft` 合成与熔炼

- [ ] 只带原木时合成工作台：材料链自动补齐（原木→木板→工作台），不再报 `recipe_not_found`
- [ ] `smelt`：熔炉里烧一样东西，产物进背包
- [ ] 不在 allowed_blocks 里的东西熔炼被拒绝

失败类别：`action_refused`、`action_unverified`。

### 7. `storage` 容器、整理、装备与丢弃

- [ ] `deposit` 存入箱子
- [ ] `sort_inventory` 整理
- [ ] `equip` 装备一件东西
- [ ] `drop` 丢弃一件东西
- [ ] 关掉容器权限后，容器类动作全部被拒

失败类别：`scope_wrong`、`action_refused`。

### 8. `survival` 进食、钓鱼与睡觉

- [ ] `eat`：饥饿值确实上升
- [ ] `fish`：在水边钓一次
- [ ] `sleep`：夜里睡床

失败类别：`action_unverified`。

### 9. `build` 蓝图放置

- [ ] `place_blueprint` 放一小组方块
- [ ] 超出确认半径的目标被拒绝
- [ ] 关掉建造权限后放置被拒绝

失败类别：`scope_wrong`。

### 10. `route_budget` 路径改方块与方块预算

这是刚补上的账目缺口，必须真实世界验证。

- [ ] 给一个需要跨越沟壑或翻越地形的目标，让寻路真的挖/搭
- [ ] 回执的 `blocks_changed` **包含路上挖掉和放下的方块**，不只是采集数
- [ ] 把 `max_blocks_changed` 设得很小（例如 8）再跑同一个目标：Joi 在到达上限那一块停下，报 `block_budget_exhausted`，**世界不再被继续改动**
- [ ] 预算用尽后，下一个改方块的目标被 Core 直接拒绝
- [ ] 路径只会破坏/放置 allowed_blocks 里的方块，且只在确认半径内

失败类别：`budget_wrong`、`scope_wrong`。

### 11. `combat_persona` 战斗人格反应（方案 A）

- [ ] 夜里被敌对生物攻击，`combat.started` 到达 UI
- [ ] 胆小人设：Joi **不主动攻击**，提议 `flee` 或 `guard`，并问用户要不要打
- [ ] 用户没有明确指令时说「上啊」这类模糊话，attack 仍被拒绝
- [ ] 用户明确说「打它」后，`attack` 才被允许，并仍走范围/预算门禁
- [ ] 全程 Joi 从不攻击玩家
- [ ] 脱离战斗后 `combat.ended` 到达

失败类别：`persona_wrong`、`action_refused`。

### 12. `screen` 屏幕证据理解意图

- [ ] 在游戏画面里指着某样东西问「这是什么」，Joi 用 `observe_screen` 回答
- [ ] 未配置视觉模型时，明说只有 OCR 文字线索，不假装看懂画面
- [ ] 麦克风披露文案与实际的截图去向一致（本机 OCR / 上传视觉模型）

失败类别：`screen_evidence_unclear`。

### 13. `plan` 多步计划审批与逐步回报

- [ ] 说「帮我去挖足够的橡木，然后建造工作台」
- [ ] 出现一张计划卡片，列出步骤，等待确认
- [ ] 确认后逐步执行，**每一步都回报进对话**
- [ ] 中途某步失败即停，不自动改计划、不重放
- [ ] 计划执行期间停止会话，计划被告知停止而不是靠失败发现

失败类别：`plan_wrong`。

### 14. `autonomy` 自主搭话与提案

- [ ] 打开自主开关，Joi 在空闲时主动说话/提问
- [ ] 台词语言跟聊天语言设置，不跟角色名
- [ ] 长任务执行中 Joi 仍会讲进度，但不会开第二个动作
- [ ] 用户说话时 ticker 让位
- [ ] 自主循环永远不提 `attack`
- [ ] 关掉开关后彻底安静

失败类别：`autonomy_noisy`、`autonomy_silent`、`language_wrong`。

### 15. `chat_channel` 游戏内聊天下指令

- [ ] scope 白名单玩家在游戏内聊天里下一条指令，Joi 执行
- [ ] 非白名单玩家的同一句话被忽略
- [ ] 聊天触发的动作同样有回执

失败类别：`scope_wrong`。

### 16. `memory` 跨会话世界记忆

- [ ] 停止会话，重新连接同一个世界
- [ ] Joi 记得这个世界有工作台/熔炉，不再提议重造
- [ ] 记忆摘要里没有坐标
- [ ] 在 `config/minecraft-skills/` 放一条玩法笔记，Joi 会按名字读取并照做

失败类别：`memory_wrong`、`coordinate_leak`。

### 17. `realtime_voice` 实时语音全链路

- [ ] 开启实时语音，接受麦克风披露
- [ ] 语音下达游戏指令，走同一套确认与回执
- [ ] 插话时旧回答与旧音频立即停止且不复活
- [ ] 语音里让 Joi 做个动作（本地动作词表）能生效
- [ ] 字幕语言跟聊天语言，朗读语言跟角色
- [ ] 记录一次「说完到出声」的主观体感（精确数字等 V2 埋点，见方向 C）

失败类别：`voice_leak`、`language_wrong`、`latency_bad`。

### 18. `recovery` 断线与超时恢复

- [ ] 长任务（分钟级）跑完不掉线，语音会话也不掉
- [ ] 主动断开世界：Joi 报告失败并进入恢复等待，**不自动重连重放**
- [ ] 恢复后需要重新明确确认才能建立新会话

失败类别：`disconnect_mishandled`。

## 收尾

18 个场景全部记录后：

```bash
.venv/bin/python tools/minecraft_walkthrough_report.py --summary
```

- 全 `pass`：切片验收成立，可在 `docs/MINECRAFT_CLOSED_LOOP_SLICE.md` §8 记一条完成。
- 有 `fail`：按类别开修复项；`coordinate_leak`、`scope_wrong`、`budget_wrong` 属隐私/权限回退，优先级高于功能问题。
- `skip` 需要写明为什么跳过（缺环境、缺权限、缺资产）。
