# Joi

## 新主线：Joi

全量 Agent 伴侣路线已切到 `agent_companion/`。旧 `mvp/` 和 `app2/` 保留为参考与素材库，不再作为主线扩写。

当前新主线包含：

- Joi Python Core：planner、policy gate、event bus、memory、character harness、tool registry。
- 工具适配器：Codex、浏览器观察队列、OK-WW game skill、MCP 配置发现、工作区文件读取。
- Tauri/Vue Shell 骨架：现代桌面 UI、角色舞台、任务卡和事件协议。

核心 smoke test：

```powershell
.\.venv\Scripts\python.exe run_agent_companion_tests.py
```

单次请求：

```powershell
.\.venv\Scripts\python.exe -m agent_companion.core.main "修复这个项目 bug 并跑测试"
.\.venv\Scripts\python.exe -m agent_companion.core.main "陪我看当前网页"
.\.venv\Scripts\python.exe -m agent_companion.core.main "帮我刷鸣潮日常"
```

---

早期原型按 Shinsekai 源项目结构收敛；当前主线已经切到 Joi。目标不是单纯做 VN，而是跑通“角色前台 + 高能力 Agent”的核心闭环：

1. `app.py` 打开设置中心，集中配置 API、角色 Prompt、系统选项。
2. 设置中心同步生成源项目风格的 `data/config/*` 和 `data/character_templates/default.txt`。
3. `chat.py` / 设置中心“启动聊天”打开桌面助手主窗。
4. 主窗用 PySide6 显示立绘、对白、输入框和工具按钮。
5. LLM 输出结构化 `dialog`，本地解析后驱动角色、表情和场景。
6. 接入源项目式 GPT-SoVITS 日语 TTS 最小链路；ASR、T2I、插件、MCP 先保留配置位置。
7. 增加最小 Agent Runtime，开始承载“任务开始 / 任务完成 / 需要授权”这类事件。

暂时没有实现完整 ASR、T2I、插件市场和 MCP 连接；这些按源项目模块边界逐步加。

当前已接入生成素材：

- `assets/nanami_sprite.png`：角色立绘
- `assets/rainy_room.png`：雨夜电脑房背景
- `assets/generated_reference.png`：原始生成参考图
- `assets/heroine_sprite.png`：更接近演示风格的角色立绘
- `assets/sakura_path.png`：更接近演示风格的樱花背景
- `assets/generated_reference_sakura.png`：樱花版原始生成参考图
- `assets/heroine_calm.png` / `heroine_smile.png` / `heroine_thinking.png` / `heroine_pout.png`：四表情立绘
- `assets/generated_reference_expressions.png`：四表情原始生成参考图

## 运行

```powershell
cd D:\codex游戏\shinsekai_mvp
py -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

也可以双击：

- `启动.bat`：打开启动菜单，可选择 MVP2、旧版聊天主窗、设置中心或创建桌面快捷方式。
- `启动MVP2.bat` / `start_chat2.bat`：打开 MVP2 Codex 助手。这个入口只做“角色前台 + Codex 执行器”的精简闭环。
- `启动聊天.bat` / `start_chat.bat`：直接打开聊天主窗。
- `启动设置中心.bat` / `start_settings.bat`：打开设置中心。
- `创建桌面快捷方式.bat`：旧版 MVP 桌面快捷方式生成器，仅保留为参考。

默认 `config.yaml` 已配置 DeepSeek 的 OpenAI-compatible 接口，真实调用失败时会自动回退本地 mock。聊天主窗输入 `开始` 即可看到对话。

API Key 推荐放在本地 `secrets.yaml`，这个文件已加入 `.gitignore`：

```yaml
llm:
  api_key: sk-your-deepseek-key
```

也可以使用环境变量 `DEEPSEEK_API_KEY`。主配置里保留了：

```yaml
llm:
  use_mock: false
  base_url: https://api.deepseek.com
  model: deepseek-v4-flash
  api_key: ${DEEPSEEK_API_KEY}
```

右上角按钮：

- `+`：新建对话，清空当前历史并回到开场。
- `H`：显示或隐藏历史面板。
- `⚙`：打开设置弹窗，切换 mock/真实模型、改 API 信息、启用 TTS，或在保存后清空历史。
- `×`：关闭窗口。

快捷键：

- 对话会自动播放完整队列；点击对白框或按空格：跳过当前打字机，或手动推进。
- `H`：显示或隐藏历史面板。
- `Ctrl+N`：新建对话。
- `Esc`：关闭窗口。

最近一次聊天会保存到 `data/chat_history/latest.json`，下次启动会恢复。默认开启 `mock_when_unconfigured: true`，真实模型失败或未配置 API Key 时会自动回退到本地 mock，并在历史里写入提示。

模型可以输出特殊角色名 `SCENE` 来切换背景，`speech` 填 `config.yaml` 里的场景名即可。例如：

```json
{"dialog":[{"character_name":"SCENE","speech":"雨夜电脑房","sprite":"1"}]}
```

七海千秋的完整同人向角色提示词放在 `prompts/nanami_chiaki_persona.md`，运行时使用的精简版已写进 `config.yaml` 的 `characters[0].setting`。

`CHOICE` 选项协议仍保留兼容，但默认 `app.enable_choices: false`，不会显示选项按钮。需要调试分支交互时可在设置中心打开“显示模型选项按钮”。

## Agent 事件骨架

聊天窗已接入注册式工具运行时。仍然可以用命令触发演示：

```text
/task 整理 README 并汇报结果
/task 安装依赖，需要提权确认
/complete 角色导入链路
/approve 打开管理员权限执行安装
/tool tools
/tool search AgentRuntime
/tool read mvp/agent_runtime.py
/tool cmd dir
/tool cmd pip install requests
```

也可以直接自然语言触发工具：

```text
有哪些工具可以用？
搜索 AgentRuntime
看一下 README.md
运行命令 dir
列出 MCP 配置
查看事件源
打开 https://example.com
打开浏览器并搜索 Joi
自搜一下自己有什么评论
观察当前页面
给当前网页截图
打开浏览器搜索七海千秋评论，然后观察当前页面
搜索 AgentRuntime 然后看一下 README.md
```

工具层现在是注册式结构：每个工具都有名称、别名、说明、参数 schema、示例和统一 `ToolResult`。当前内置工具包括工具清单、工作区文本搜索、文件读取、命令执行、MCP 配置查看、插件配置查看、事件源查看。安装、下载、删除、启动外部程序等操作会先被拦截成“需要授权”的角色事件；用户点 Yes 后才会继续执行，点 No 会取消。

浏览器工具现在会启动本地 Browser Executor，基于 PySide6 QtWebEngine 打开一个真实浏览器窗口并直接执行动作。角色工具会把请求写入 `data/agent_events/browser_requests.jsonl`，等待执行器把结果写回 `data/agent_events/browser_responses.jsonl`，然后在“工具结果”面板显示标题、页面文本或截图路径。已注册的浏览器工具包括 `browser.open_url`、`browser.search`、`browser.search_extract`、`browser.observe`、`browser.screenshot`、`browser.click`、`browser.type_text`、`browser.extract_text`。

这层执行打开网页、截图、点击、输入和提取页面文本时不额外消耗 DeepSeek 或 Codex token；只有你让角色理解复杂自然语言、总结网页内容或继续规划下一步时，才会回到模型调用。

成功用过一次浏览器后，后续“再搜 / 再找 / 查一下”会默认继续走浏览器搜索；如果要搜本地项目，请明确说“搜索项目里的 XXX”或“搜索文件 XXX”。

`browser.observe` 是视觉层 MVP：抓当前浏览器截图，记录视口尺寸、滚动位置、可见元素坐标和文字。`browser.search_extract` 用于视频演示式闭环：先打开搜索页，再做视觉观察和页面文本提取，最后把观察结果喂给角色模型生成“看到页面后的反应”。例如“自搜一下自己有什么评论”“查一下七海千秋百科，然后说说你看到什么”。

如果配置了支持图片输入的 OpenAI-compatible 模型，观察反应会把浏览器截图作为图片输入一起发送；否则使用截图路径、可见元素坐标和页面文本摘要生成反应：

```yaml
llm:
  vision_enabled: true
  vision_base_url: https://api.example.com/v1
  vision_model: your-vision-model
  vision_api_key: ${VISION_API_KEY}
```

工具完成后，聊天窗会在左侧显示“工具结果”面板；角色语音只播报自然摘要，不朗读工具名、路径、JSON、命令输出等机器文本。

## Agent 任务

聊天输入现在会先尝试识别多步任务。带有“然后 / 再 / 接着 / 顺便 / 最后”等连接词的目标会被拆成一个本地 Agent Plan，按顺序调用注册工具，最后汇总结果。示例：

```text
打开浏览器搜索七海千秋评论，然后观察当前页面
搜索 AgentRuntime 然后看一下 README.md
```

也可以用 `/agent` 强制走 Agent 执行器，即使只规划出一个工具步骤：

```text
/agent 观察当前页面
```

当前 Planner 是确定性规则版，复用工具注册表和参数 schema；后续可以替换成 LLM Planner，但执行仍走同一套工具权限、结果标准化和事件回写。

## Codex Executor

角色前台现在有一条 Codex 适配器边界：

- `codex.submit_task`：写入 `data/agent_events/codex_requests.jsonl`，可选启动本机 `codex exec`。
- `codex.status`：查看最近 Codex 任务和执行器回写状态。
- `codex.start_task`：启动已提交的任务，支持指定 `task_id` 或启动最近一条。
- `codex.cancel`：写入 `data/agent_events/codex_cancellations.jsonl`，供执行器监听。
- `data/agent_events/codex.jsonl`：Codex 执行器回写事件，聊天窗会自动播报。

自然语言示例：

```text
让 Codex 修复浏览器观察失败的问题
查看 Codex 任务状态
启动最近的 Codex 任务
修复项目里的 bug 并跑测试
```

默认只提交队列，不自动消耗 Codex token。明确说“直接启动 / 立即启动 / 直接跑 / 自动启动 / 现在执行”时，会通过 `run_codex_task.py` 后台调用本机 `codex exec`：

```text
让 Codex 直接启动检查 README
```

也可以显式调用：

```text
/codex 修复项目里的 bug 并跑测试
/tool codex.submit_task {"goal":"编译项目并修复失败","auto_start":true}
/tool codex.start_task {"latest":true}
```

执行端 wrapper 会把进度和结果写入：

- `data/codex_runs/<task_id>.jsonl`
- `data/codex_runs/<task_id>.stderr.log`
- `data/codex_runs/<task_id>.final.txt`

Runtime 会监听这些本地 JSONL 事件源，用于后续接 Codex、MCP 和插件：

- `data/agent_events/inbox.jsonl`
- `data/agent_events/codex.jsonl`
- `data/agent_events/codex_requests.jsonl`：Codex 任务请求队列
- `data/agent_events/codex_cancellations.jsonl`：Codex 取消请求队列
- `data/agent_events/mcp.jsonl`
- `data/agent_events/plugins.jsonl`
- `data/agent_events/browser.jsonl`
- `data/agent_events/browser_requests.jsonl`：浏览器请求队列，不是结果事件源
- `data/agent_events/browser_responses.jsonl`：本地 Browser Executor 结果回写

也可以直接在终端看事件流：

```powershell
.\.venv\Scripts\python demo_agent_events.py
.\.venv\Scripts\python demo_agent_tools.py
.\.venv\Scripts\python demo_natural_tools.py
.\.venv\Scripts\python run_browser_executor.py
```

外部执行器也可以写入本地事件 inbox，聊天窗会自动轮询并播报：

```powershell
.\.venv\Scripts\python emit_agent_event.py complete "角色包导入" "角色包已经导入完成。"
.\.venv\Scripts\python emit_agent_event.py approval "安装依赖" "需要允许执行 pip install。" --detail "pip install -r requirements.txt"
.\.venv\Scripts\python emit_agent_event.py complete "Codex 任务" "任务已经完成。" --source codex
.\.venv\Scripts\python emit_agent_event.py complete "浏览器任务" "网页已经打开。" --source browser
```

## 日语语音

当前 MVP 按源项目思路接了 GPT-SoVITS，并支持角色级语音档位：

- `tts.provider: gpt-sovits`
- `tts.server_url: http://127.0.0.1:9880/`
- 全局 `tts.text_lang` 只是默认值；角色下的 `voice_profiles` 可以覆盖朗读语言、参考语言、模型、参考音频和参考文本。
- 设置中心“角色管理”页可以切换当前角色的语音档位。
- 爱弥斯当前使用 `zh` 中文档；七海千秋保留 `ja` 日语档。

默认 `fallback_to_system: false`，所以 GPT-SoVITS 没有成功生成时会静音并在历史里提示，不会播放中文系统音。模型输出里支持 `translate` 字段作为日语朗读稿，UI 显示仍用中文 `speech`。

生成的 WAV 会做短淡入淡出处理，用来减轻开头或结尾的轻微爆音/电流感。

## .char 角色包

设置中心的“角色管理”页已经支持导入源项目 `.char` 包。导入器兼容源项目导出的 zip 结构：

- `character.yaml`
- `sprites/<sprite_prefix>/...`
- `speech/<sprite_prefix>/...`
- `models/...`

导入后会把角色资源复制到 `data/sprite`、`data/speech`、`data/models`，把导入角色放到当前角色列表第一位，并自动设置：

- `tts.enabled: true`
- `tts.provider: gpt-sovits`
- `tts.text_lang: ja`
- `tts.fallback_to_system: false`

也可以用命令行导入：

```powershell
.\.venv\Scripts\python import_char.py path\to\nanami.char
```

源项目当前资源索引里的“七海千秋”角色包是百度网盘链接，不是 GitHub 直链；下载到本地后用上面的入口导入即可。

## 角色切换

设置中心“角色管理”页里的“当前角色”下拉框可以切换角色。切换时会把选中的角色移动到 `config.yaml` 的 `characters[0]`，同步 `data/config` 和角色模板；聊天窗需要重开或从设置中心重新启动才会加载新角色。

每个角色可以绑定自己的语音档位，例如当前配置里：

- 爱弥斯：中文 `zh`
- 七海千秋：日语 `ja`

## 与源项目的对应关系

| 源项目模块 | 当前 MVP 对应 |
| --- | --- |
| `webui_qt.py` 设置中心 | `app.py` + `mvp/settings_center.py` |
| `main.py` 聊天主窗 | `chat.py` + `mvp/ui.py` |
| `data/config/api.yaml` | 设置中心自动同步 |
| `data/config/characters.yaml` | 设置中心自动同步 |
| `data/config/system_config.yaml` | 设置中心自动同步 |
| `data/config/background.yaml` | 设置中心自动同步 |
| `data/character_templates/*.txt` | `data/character_templates/default.txt` |
| TTS 适配器 | `gpt-sovits` 日语 WAV 生成 + QtMultimedia 播放，默认失败静音 |
| LLM/TTS/UI worker 管线 | 当前为轻量线程池实现，下一步拆成三段队列 worker |
| 插件/MCP | 已生成占位配置，下一步接工具注册 |

## 下一步

推荐按这个顺序扩展：

1. 把当前 LLM 调用拆成源项目式 `LLMWorker -> TTSWorker -> UIWorker` 三段队列。
2. 增加 `.char` 导出。
3. 接入 ASR 麦克风按钮的最小后端。
4. 把 TTS 扩展成可注册的角色级适配器接口。
5. 再做插件接口和 MCP 工具注册。
