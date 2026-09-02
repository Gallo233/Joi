# 需要你亲自动手的发布步骤

> 状态：操作手册，2026-09-02 第三版。分发模型已定：**从 GitHub Releases 发，不上 App Store，不做 Apple 公证**，下载者第一次打开时自己放行；Live2D Expandable Application 不申请。
> 这一轮把清单上除干净机走查以外的每一项都做完了。**只剩一项。**
> 配套：`docs/MACOS_RELEASE.md`（流程权威）、`docs/INSTALL_MACOS.md`（给下载者）、`docs/RELEASE_READINESS_2026-08-18.md`（逐项证据）。

---

## 只剩一项

| 阻塞项 | 谁能做 | 现状 |
|---|---|---|
| **干净机验收** | **只有你** | ❌ 需要一台没有开发环境的 Apple Silicon Mac |

走完它，把 GitHub 上的 draft release 点成正式发布，就发出去了。

### 已完成（这一轮）

| 项 | 证据 |
|---|---|
| 出包与签名 | `tools/build_macos_release.py` 跑通；ad-hoc 签名封装完整，`codesign --verify --deep --strict` 通过 |
| draft release | 已建，DMG 已上传，说明用的是 `docs/INSTALL_MACOS.md`，**停在 draft** 等走查 |
| 版本号与 tag | 定为 `v0.1.0`；CHANGELOG 的 `Unreleased` 收成 `0.1.0 — 2026-09-02`；`packaging_smoke` 校验四处版本一致 |
| 隐私声明 | 去掉 Draft，改写成「这个构建做什么」而不是「发布前必须做什么」；补上无遥测、无崩溃上报的实测结论 |
| 安全报告渠道 | 新增 `SECURITY.md`，隐私声明里那句「渠道配置好之后」有了实际去处 |
| 应用内版权声明 | 设置里新增「关于」页：版本、许可证全文、第三方通知全文、Live2D 版权声明、数据目录位置。`packaging_smoke` 与 Shell 测试各有一道门禁 |
| 字体与声音分发权 | **审计结论：不存在这个问题** —— 见下 |

### 字体与声音：审计结果

原清单把这条记成「待确认」，实际查下来**发布物里既没有字体也没有音频**：

- 仓库和 `.app` 里都没有任何 `.ttf` / `.otf` / `.woff2`。界面用的是系统字体栈（`PingFang SC`、`-apple-system`），不嵌入、不分发。
- `.app` 里没有任何 `.wav` / `.mp3`。GPT-SoVITS 的参考音频由用户自己提供，不随包分发；本机 `data/agent_companion/voice/` 下的音频是运行时缓存，不进包。

所以没有需要确认的分发权。仍然要留意的是 **JoiDebug Web 种子资产**（VRMA 样例声明「不得导出或分享」）—— 那条挡的是 Web 发布，不挡桌面版。

---

## 剩下这一项：干净机验收（2–3 小时）

需要一台**没有 Python / Node / Rust / 仓库 checkout / Joi 配置**的 Apple Silicon Mac。不能用开发机 —— 装着的东西会掩盖真实首次运行的问题。新建一个 macOS 用户账户是最省事的近似，但系统级依赖仍在，属于打折的证据，记录时写明。

从 draft release 下载 DMG，然后：

### 1. 放行流程（这一轮新增的验收项）

按 `docs/INSTALL_MACOS.md` 一步步走。我写的是：macOS 15 及更新的系统上「右键 → 打开」已被 Apple 取消，正确路径是 **系统设置 → 隐私与安全性 →「仍要打开」**。

**确认系统弹出的措辞和点击路径与文档一致。不一致以你看到的为准，告诉我，我改文档。** 这是下载者会遇到的第一件事，写错了比任何功能 bug 都劝退。

同时确认：**不应**出现「已损坏，应移到废纸篓」。出现了说明签名封装出了问题 —— 这正是这一轮显式 ad-hoc 签名要解决的。

### 2. Hero Journey

基础配置 → 与默认角色的一次真实对话 → 进入项目 → 观察一个可信窗口 → Joi 提出一个非敏感动作 → 逐步确认 → 执行并重新观察验证 → 生成一条待确认记忆 → 你确认它。

这条链路是 PRD §5.2 冻结的首发主线，**它跑不通比任何 Beta 能力出问题都严重**。

### 3. 冷启动计时

记下从双击到窗口可交互的秒数。TDD §13 预算是 p95 ≤ 10s，而首次启动是冷缓存：本机 sidecar 冷启 23.1s、热启 0.5s，600MB 的包在没预热的机器上是真实风险。**这个数只有你能量**，也是这次走查唯一拿得到的性能证据。

### 4. 关于页

打开 设置 → 关于，确认：版本号显示 `0.1.0`、能展开许可证与第三方通知全文、Live2D 版权声明在、数据目录路径与实际一致。

### 5. 其余各项

- **日志检查** —— `~/Library/Application Support/com.gallo233.joi/logs/` 里要**有**可用的启动错误，且**没有** API key、提示词原文、本地文件内容、session token。
- **权限撤销** —— 关掉辅助功能和屏幕录制，让 Joi 去做需要它们的事：应当明确说出缺哪个权限，**不应**反复重试、卡住或假装成功。
- **多屏与缩放** —— 接一台缩放比例不同的外接屏，做一次 Computer Use 点击，确认点到的是你看到的位置。这条从未在第二块物理屏上跑过。
- **VoiceOver** —— `Cmd + F5`，用键盘走一遍：**审批卡片能否被读出、能否被键盘操作最关键**，审批是安全边界，不可达等于不可用。
- **升级与回滚** —— 这次是第一个版本，没有旧版可覆盖；记为 N/A，留到 0.2.0。
- **BYOK / 附件 / 角色切换与卸载 / 记忆隔离 / 退出重开** —— 各走一遍。

**记录方式**：每项写 pass/fail + 一句话，**不要**贴截图、路径、日志原文、账号信息。写完发我，我更新 `docs/RELEASE_READINESS_2026-08-18.md` 与 `docs/KNOWN_ISSUES.md`，然后你把 draft 点成正式发布。

---

## 发布之后要用到的两条命令

重出包（改了代码之后）：

```bash
.venv/bin/python tools/build_macos_release.py
```

换掉 draft 里的 DMG：

```bash
gh release upload v0.1.0 "agent_companion/shell/src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/Joi_0.1.0_aarch64.dmg" --clobber
```

**每次换包都要把新的 SHA-256 写进 release 说明**（`build_macos_release.py` 会打印），否则下载者核对不上。

---

## 不阻塞发布，记在这里

### 合回 main

`minecraft-slice` 领先 `origin/main` 122 个 commit，CI 只在 push 到 `main` 和 PR 时运行 —— 这批代码在 GitHub 上一次都没跑过。本地出包不经过 CI，所以它不挡发布；合并后 CI 会在干净 checkout 上再验一遍，能发现「只有你机器上有」的依赖。

```bash
gh pr create --base main --head minecraft-slice --title "Joi 0.1.0" --body "See docs/CHANGELOG.md 0.1.0."
```

### CI 出包（可选）

`release-macos.yml` 不再强制 Apple secrets：缺了就 ad-hoc 签名并在日志里 warning。它仍需要两个资产 secret，因为 runner 上没有授权资产；ZIP 已经打好在 `~/Desktop/joi-release-assets.zip`。走本地出包就完全用不上。

### Minecraft 真实服走查

24 个动作里只有最早的 10 个在真实世界跑过，PRD §5.2 把游戏适配器划在 Beta 轨道。最该盯的三个场景：`route_budget`、`combat_persona`、`coordinate_leak`（回执/字幕/语音/记忆里出现坐标即为隐私回退）。

### Joi Web 上线

另一条发布线，从未在真实 VPS 上跑过 Caddy 后面；加上种子资产里 VRMA 样例「不得导出或分享」那条权利问题。
