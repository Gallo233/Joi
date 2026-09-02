# Joi Debug Voice Latency Investigation — 2026-08-13

## 结论

旧点击录音链路同时有两个问题：

1. 产品计时错误：`voice.transcribe` 等待 ASR 后又同步等待完整 Joi turn，所以“转写中”包含 planner、LLM、工具和可能的 TTS 时间。
2. 当前 Debug ASR provider 本身也慢：真实请求的中位数约 4.33 秒，不是把 RPC 拆开就会消失的本地开销。

因此答案不是“只怪 LLM”或“只怪 ASR”。旧 UI 把两段串成一段；当前 provider 实测显示 ASR 和文本模型都贡献了秒级延迟。

## 方法与结果

测试使用 Joi Debug 实际解析后的非敏感运行状态；没有输出 key、endpoint 或用户音频。ASR 输入是 macOS 系统语音生成的 3.62 秒、16 kHz、单声道 PCM WAV，文本为一条合成测试句。

| 阶段 | 结果 |
| --- | --- |
| ASR 配置 | enabled/configured，MiMo `mimo-v2.5-asr`，中文 |
| ASR provider 三次 | 6081 ms、4058 ms、4326 ms |
| ASR 中位数 | 4326 ms |
| ASR 正确性 | 三次都完成，但把“Joi”识别成“Siri” |
| 文本模型有效短响应 | 首个 reasoning 增量 2545 ms，首个可见 token 5100 ms，总计 5102 ms |

一次人为设置为 8 token 的文本模型探针在 16.44 秒后没有可见内容，属于不合适的 reasoning token 上限，不计入正常短响应基准。上述数字只代表当时网络、账号与 provider 状态，不是 SLA；应在真实麦克风、口音、噪声和常用句上继续采样。

## 本轮改变

- ASR 完成即返回 transcript，普通 Joi turn 后台排队；用户可先看到识别结果。
- Developer 模式显示安全的 `provider_ms` 与停止录音到 transcript 的端到端时间，并保留 prepare/encode/decode/RPC 分段供诊断。
- 新输入、输入切换、取消或换 thread 会退休旧 ASR generation；迟到 transcript 不会成为新命令。
- 实时语音实验改用 WebRTC speech-to-speech，以 VAD、流式 transcript 与 barge-in 避免“录完整段 → 上传 → ASR → LLM → TTS”的全串行等待。

## 下一轮测量建议

在 Developer 模式用同一组 10 条真实中文短句各跑 3 次，记录：

- `provider_ms` 的 P50/P95；
- 停止说话到 transcript 的 P50/P95；
- 关键词“Joi”、中英混说、数字/日期的错误率；
- transcript 出现到首个可见回答、首音频的时间；
- Realtime 的 speech-stopped → assistant audio-started，以及插话停止音频的时间。

先把点击录音 ASR P50 目标设为 ≤ 2 秒、P95 ≤ 4 秒；若 MiMo 在代表性网络下仍明显超标，再用相同语料对比 Realtime transcription 或另一家 file transcription provider，而不是靠缩短 timeout 掩盖慢请求。
