# Realtime Voice Debug

## 两条语音链路

1. 点击录音 ASR：转写完成即返回 Shell，普通 Joi turn、LLM、工具和 TTS 在后台继续；开发者延迟拆分仍可用来判断慢在本地编码、网络 ASR 还是下游 LLM。
2. Realtime：Shell 只采集并发送 16 kHz / PCM16 / mono、40 ms 一帧的音频；Core 持有阿里云凭据，与 `qwen-audio-3.0-realtime-flash` 建立 WSS 会话并只接受文本输出；Joi 使用本地 GPT-SoVITS 发声。

Realtime 不复用点击录音 ASR，也不把 Qwen 的音频作为 Joi 声音。GPT-SoVITS 不可用时保留字幕并静音，不回退到系统或云端音色。

## 三种语言

界面语言和聊天语言在「设置 → 语言」里选，存在 config.yaml 的 `language` 段；说话语言来自角色包，在角色库里跟音色一起选。

普通对话本来就有两条文本通道：屏幕文字用聊天语言，朗读文本由 expression 通道按角色配音语言另写一份。Realtime 只有一条通道，所以两种语言不同时，Core 在 `session.update` 的 instructions 里要求 provider 每轮输出两行：

```
朗读：<配音语言的那句话>
字幕：<同一句话，用聊天语言写>
```

Core 拆开后，朗读行进 TTS，字幕行进聊天框。两种语言相同时不拆，只发一行。模型不按格式输出时，整段文字同时用于朗读和字幕，不会丢内容；拆分模式下不再转发流式 delta，字幕在 `response.done` 时整句出现。

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

Qwen 才能提出十种严格 `GameIntent` 之一。Core 自行绑定 session、generation 和 goal，模型不能提供权限、scope、approval、budget 或任何 Core ID。只有完整 mic turn 与完成的 provider response 能提交动作；同一 turn 最多一条。所有动作继续经过 Core 的权限、实时 scope、预算、Bridge 和回执门禁。

插话只取消旧回答和旧 TTS，不自动取消已提交的游戏动作；UI 提供暂停、继续、取消。结束 Realtime、窗口/Core transport 丢失或 provider 断开会取消当前游戏目标；ACK 超时强制结束 Bridge 并进入恢复等待，绝不自动重放。

## 手工烟测

1. 启动 debug Shell，运行状态应显示 Qwen Audio Realtime 已配置、本地 GPT-SoVITS 输出。
2. 开始普通实时语音，接受在线麦克风披露，说一句短句，确认字幕和本地角色声线。
3. Joi 发声时插话，确认旧 PCM/口型立即停止且不复活。
4. 拒绝麦克风、断网和关闭窗口，确认 provider/Bridge 都没有遗留会话，UI 不出现原始 provider event/id/error。
5. Minecraft 真实服 smoke 需 HMCL 启动一个 Java 世界并“对局域网开放”，把屏幕显示的端口填入 Skill；逐项测试 observe/inventory/follow/come/collect/mine/craft/eat/place/deposit 与 disconnect recovery。

## 官方参考

- [Qwen Audio Realtime](https://help.aliyun.com/zh/model-studio/fun-audiochat-realtime)
- [WebSocket API](https://help.aliyun.com/zh/model-studio/fun-audiochat-realtime-websocket-api)
- [客户端事件](https://help.aliyun.com/zh/model-studio/fun-audiochat-client-events)
- [服务端事件](https://help.aliyun.com/zh/model-studio/qwen-audio-realtime-server-events)
- [Mineflayer](https://github.com/PrismarineJS/mineflayer)
- [mineflayer-pathfinder](https://github.com/PrismarineJS/mineflayer-pathfinder)

Joi 借鉴其流式事件、VAD、pathfinder 和 Bot 状态观测；权限、审批、预算、回执、隐私和 no-replay 仍由 Joi Core 独立实现。
