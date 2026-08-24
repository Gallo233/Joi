# Joi Web 体验架构

Joi Web 是个人网站上的匿名体验面，不是桌面 Joi 的多租户版本。每位访客由
broker 拉起一个短生命周期 Core 和一个独立 workspace；完整 Shell 与微缩桌宠是
同一份 Vue 应用的两个 iframe，使用同一 token 连接同一 Core。Core 原有的事件
广播因此天然同步两种视图，不新增第二套对话状态。

```text
personal site
  /joi page
    ├─ iframe ?mode=full&guest=1
    └─ iframe ?mode=compact&guest=1
               │ shared wss session + token
               ▼
Caddy (TLS only)
               ▼
loopback broker
    ├─ /ws/<session>       → Core port
    ├─ /assets/<session>/* → Core port + 1
    └─ one Core + workspace per visitor
```

## 浏览器边界

`shell/src/platform/` 收敛 Tauri 与浏览器差异。桌面构建仍通过 Tauri 取得 Core、
附件、资源 URL 和窗口能力；Web 构建只从 iframe query 读取 `core`、`token`、
`guest`、`mode` 与明确的父页面 origin。父子页面消息都校验 source、origin 和
固定的 `source` 字段。

完整与微缩 iframe 不能互相改对话状态。父页面只负责几何和导航：

- IntersectionObserver 决定完整视图滚出后是否显示桌宠；
- compact Shell 上报尺寸、拖动增量和恢复位置；
- 父页面约束桌宠至少 80px 可抓取区域仍在视口内；
- compact 的“记忆”等导航请求由父页面转交给完整 iframe；
- query token 使用 `no-referrer` iframe 与页面策略，避免出现在静态资源 referrer。

访客只显示 chat、characters 和只读 memory。附件、看屏、设置、BYOK、角色导入/
编辑/卸载、本机与游戏入口均隐藏。这只是体验层，安全性不依赖这些条件渲染。

## 服务端边界

broker 只绑定 loopback。它按 Origin 和 HMAC 后的 IP 日计数创建会话，端口池步长
至少为 2，因为每个 Core 固定占 WS port 和 `port + 1` 资源 port。它复制由
`prepare_seed.py` 生成的干净 seed，显式移除继承的 `JOI_DATA_HOME`，再通过
ready file 等待 Core 身份和就绪状态。TTL、进程退出或已认证的 DELETE 都终止
进程并删除临时 workspace。

角色 seed 只读取 JoiDebug 的 `characters/packages`，不读取 `characters/runtime`
或个人 workspace 的其他目录。当前 Web 白名单完整复制日和 Live2D、
AvatarSample_A VRM/VRMA、Miku MMD/VMD 与 Cat girl 立绘四个包；星野澪和
Seed-san 不进入 seed。包内模型、动作、表情、readme 与清单不做静默裁剪，
Core 列表 RPC 把模型路径转换成带会话令牌的资源 URL，浏览器才能为没有独立头像
的 Live2D、MMD 和立绘角色生成缩略图。

Caddy 只做 TLS、公共路径选择和反向代理。WebSocket 在 TLS 终止后仍是帧协议，
不能靠 Caddy 配置可靠解析 JSON-RPC method；因此 RPC 白名单位于 Core 的 token
认证之后、router dispatch 之前。被拒绝的方法与未知方法同为 `-32601`，不暴露
本地能力表。guest ready payload 同样只投影角色、对话/记忆上下文、语音状态与
公开健康字段。

公开方法只覆盖对话、角色读取/激活、只读记忆、隔离 workspace 内的 thread、
语音和健康检查。Realtime 的 Minecraft control 另有 Core guest 拒绝，即使以后
错误扩大了通配符也不能进入本机/游戏路径。访客 config 同时关闭 Agent CLI、
Codex、browser、Computer Use、watch、游戏、runtime config、local files 和 MCP，
形成独立于 RPC 表的策略纵深防御。

## 费用护栏

所有文本模型调用经过 `model_call.execute_call`。Guest Core 在每次 provider attempt
之前按输入估算 + 最大输出预留 token；预留失败时不会调用 provider。成功响应用
provider usage 结算，没有 usage 时保守估算；provider failure 保留全部预留，因为
失败请求也可能计费。单会话计数在 Core 内，全站 UTC 日计数写入只含聚合数字的
共享锁文件。

Realtime Coordinator 在 provider session 建立前原子预留时长，同一访客 Core 的
两个 iframe 最多一个 active owner。Core 的 timer 到时执行 `stop_owner(...,
"guest_time_limit")`；正常提前结束只结算实际秒数并退回其余全站预留。前端倒计时
读取 Core 上限用于说明和反馈，但不是闸门。

默认值及部署流程记录在 `agent_companion/web/README.md`。生产上线前仍需在目标
Linux、真实域名、真实 provider 与个人网站上验证 Caddy、systemd、麦克风权限、
额度熔断和容量；本地通过不等于签名、TLS 或线上容量通过。
