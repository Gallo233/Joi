# Realtime Voice Debug

## 两条语音链路

1. 点击录音 ASR：转写完成即返回 Shell，普通 Joi turn、LLM、工具和 TTS 在后台继续；开发者延迟拆分仍可用来判断慢在本地编码、网络 ASR 还是下游 LLM。
2. Realtime：Shell 只采集并发送 16 kHz / PCM16 / mono、40 ms 一帧的音频；Core 持有阿里云凭据，与 `qwen-audio-3.0-realtime-flash` 建立 WSS 会话并只接受文本输出；Joi 用她自己的角色声音发声。

Realtime 不复用点击录音 ASR，也不把 Qwen 的音频作为 Joi 声音，也不回退到系统音色。

角色声音就是这台 Joi 平时说话的那一个：本机 GPT-SoVITS，或角色配置里那个云端音色。早先这里只认 GPT-SoVITS。桌面上两者重合，看不出区别；Web 访客的角色声音本来就是云端音色，于是整场实时语音只有字幕没有声音。判断标准因此改成「是不是 Joi 自己的声音」，而不是「是不是某一种合成实现」——云端音色意味着她的回答文本也会发给该语音服务，启动前的披露会说明这一点。角色声音不可用或合不出声音时，仍然保留字幕并明确静音。

## 三种语言

界面语言和聊天语言在「设置 → 语言」里选，存在 config.yaml 的 `language` 段；说话语言来自角色包，在角色库里跟音色一起选。

开发者模式会显示每一轮的时延明细（静音→首字→成文→出声）与本次通话的 P50/P95；只有毫秒数和轮数，没有原文、ID 或 provider 细节。

普通对话本来就有两条文本通道：屏幕文字用聊天语言，朗读文本由 expression 通道按角色配音语言另写一份。Realtime 只有一条通道，所以两种语言不同时，Core 在 `session.update` 的 instructions 里要求 provider 每轮输出两行：

```
朗读：<配音语言的那句话>
字幕：<同一句话，用聊天语言写>
```

Core 拆开后，朗读行进 TTS，字幕行进聊天框。两种语言相同时不拆，只发一行；拆分模式下不再转发流式 delta，字幕在 `response.done` 时整句出现。

聊天语言可以设成「跟随」，意思是每一轮都用用户这一轮说话的语言。它是一个设置项，不是一种语言，所以必须在拿到本轮转写之后就地解析成具体语言，绝不能当成 locale 往下传。以前它被原样传给了字幕改写调用，等于让文本模型「把这句话改写成 follow」，模型自己挑了一种——中文提问收到英文回答就是这么来的。解析之后有三种结果：用户说的正是角色的配音语言，就不需要第二种语言，朗读行直接当字幕；用户说的是能点名的另一种语言（中/日/韩/英），才值得花一次改写调用；其余（只凭拉丁字母无法确定是哪种语言）宁可显示朗读行，也不改写成一个猜测。

第一种结果里，模型多半仍然给了第二行——两行格式是会话建立时定死的，那时谁都还没说话，没法知道这一轮用不用得上。被要求「用用户说话的语言写字幕」而那正是它此刻在说的语言，它就去翻译了。所以这一行不只是不需要，是必须不显示：访客听到中文、读到英文，就是它上了屏。

模型只给一行、或者第二行仍然写在配音语言里时，Core 会用一次短文本调用把它改写成上面解析出的那种语言。这条调用在音频链路之外，声音不等它；失败就显示朗读行本身，因为语言不对的字幕也好过没有字幕。

合成前还有一层兜底：当文本明显不是所选配音语言时，按它实际书写的语言发音，而不是用配音语言的读法逐字念。中日共用汉字，因此只在能确定的情况下纠正（假名只属于日语，成段汉字且无假名不是日语），角色的权重和参考音频始终不变。

Realtime 的用户语音识别结果和 Joi 的字幕都会作为普通对话气泡出现在聊天框里。这些气泡由 Shell 在本地生成，不写入 Core 的会话历史——Realtime 会话仍然是临时的。

## 配置和密钥

```yaml
realtime_voice:
  enabled: true
  provider: qwen_audio
  url: wss://dashscope.aliyuncs.com/api-ws/v1/realtime
  model: qwen-audio-3.0-realtime-flash
  api_key: ${JOI_QWEN_REALTIME_API_KEY}
  turn_detection: server_vad
  threshold: 0.5
  silence_duration_ms: 500
  max_history_turns: 8
  timeout_seconds: 15
```

生产 debug 包通过 `tools/import_qwen_realtime_secret.py` 把控制台 CSV 的 `apiKey` 导入 macOS Keychain。长期 key 不进入 Shell、URL/query、状态事件、日志、SQLite、Bridge 子进程或应用包。

## Minecraft 模式

普通 Realtime 模式没有工具。只有用户先在 Minecraft Skill 中：

1. 配置 HMCL/LAN 连接；
2. 选择“一起玩”或“单独玩”；
3. 精确确认 server/world/dimension/起点半径/玩家/方块/建造与容器权限/动作和改块预算；
4. 建立持久 Minecraft session；
5. 再显式选择“实时语音 + Minecraft”；

Qwen 才能提出一条严格 `GameIntent`（19 个原语加 4 个只读查询）或一份计划。Core 自行绑定 session、generation 和 goal，模型不能提供权限、scope、approval、budget 或任何 Core ID。只有完整 mic turn 与完成的 provider response 能提交动作；同一 turn 最多一条。所有动作继续经过 Core 的权限、实时 scope、预算、Bridge 和回执门禁。

插话只取消旧回答和旧 TTS，不自动取消已提交的游戏动作；UI 提供暂停、继续、取消。结束 Realtime、窗口/Core transport 丢失或 provider 断开会取消当前游戏目标；ACK 超时强制结束 Bridge 并进入恢复等待，绝不自动重放。

## 手工烟测

1. 启动 debug Shell，运行状态应显示 Qwen Audio Realtime 已配置、角色声音输出。
2. 开始普通实时语音，接受在线麦克风披露，说一句短句，确认字幕和本地角色声线。
3. Joi 发声时插话，确认旧 PCM/口型立即停止且不复活。
4. 拒绝麦克风、断网和关闭窗口，确认 provider/Bridge 都没有遗留会话，UI 不出现原始 provider event/id/error。
5. Minecraft 真实服走查见 `docs/MINECRAFT_REAL_SERVER_WALKTHROUGH.md`：18 个场景覆盖全部动作、路径改方块与预算、战斗人格、屏幕证据、计划、自主、游戏内聊天与断线恢复。离线可先跑 `tools/minecraft_p5_smoke.py`。

## 官方参考

- [Qwen Audio Realtime](https://help.aliyun.com/zh/model-studio/fun-audiochat-realtime)
- [WebSocket API](https://help.aliyun.com/zh/model-studio/fun-audiochat-realtime-websocket-api)
- [客户端事件](https://help.aliyun.com/zh/model-studio/fun-audiochat-client-events)
- [服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-realtime-server-events)
- [Mineflayer](https://github.com/PrismarineJS/mineflayer)
- [mineflayer-pathfinder](https://github.com/PrismarineJS/mineflayer-pathfinder)

Joi 借鉴其流式事件、VAD、pathfinder 和 Bot 状态观测；权限、审批、预算、回执、隐私和 no-replay 仍由 Joi Core 独立实现。
