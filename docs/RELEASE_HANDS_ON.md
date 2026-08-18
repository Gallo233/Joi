# 需要你亲自动手的发布步骤

> 状态：操作手册。只写**我做不了、必须由你完成**的事：需要你的账号、你的机器、你的决定或你的权利确认。
> 配套：`docs/RELEASE_READINESS_2026-08-18.md`（逐项证据）、`docs/MACOS_RELEASE.md`（流程权威）、`docs/MINECRAFT_REAL_SERVER_WALKTHROUGH.md`（B1 走查）。
> 每一步末尾写了「完成后我能接手做什么」。

---

## 步骤 0：把这条分支并回 main（10 分钟）

**为什么必须先做**：CI 只在 push 到 `main` / `win-desktop-fixes` 和 PR 时运行。本轮补的门禁（Shell 测试、Node 22、桥接构建与离线烟测）现在一次都没在 GitHub 上跑过。`minecraft-slice` 领先 `origin/main` 54 个 commit。

```bash
git push -u origin minecraft-slice
```

```bash
gh pr create --base main --head minecraft-slice --title "Minecraft closed-loop slice + release gates" --body "See docs/CHANGELOG.md Unreleased."
```

然后在 PR 页面等两条 lane 变绿：

- **macOS required lane** —— 这是主平台门禁。第一次会跑新加的 Shell 测试与桥接构建，**如果 `npm ci` 在 bridge 目录失败或 ncc 构建超时，先告诉我，不要直接合**。
- **Windows compatibility lane** —— 兼容路径，允许比 macOS 慢。

绿了之后：

```bash
gh pr merge --squash --delete-branch=false
```

> 分支上的 54 个 commit 都有独立的提交信息，`--squash` 会把它们压成一条。想保留历史就用 `--merge`。这是你的偏好，我不替你定。

**完成后我能接手**：任何 CI 失败的修复。

---

## 步骤 1：定软件许可证（决定 30 分钟，落地由我做）

**现状**：仓库是 **PUBLIC**，且**没有 LICENSE 文件**。在版权法下这等于「保留所有权利」—— 任何人都不能合法地复制、修改或分发它，包括你自己未来想引入的贡献者。这是一个正在生效的状态，不是待办事项。

**依赖侧的约束（我已核实）**：

- 绝大多数依赖是 MIT / Apache-2.0 / BSD / ISC，对你的选择**没有约束**。
- **PyInstaller** 是 GPL-2.0-or-later，但带 **bootloader exception**：用它冻结的应用可以按任何许可证分发，包括闭源。不构成约束。
- **Live2D Cubism Core**（`live2dcubismcore.min.js`）是 Live2D 公司的专有许可，**不是**开源件。它不能被你的仓库许可证覆盖，必须单独声明，且它的分发条款独立于你选什么。
- Numen 的核心是 LGPL-3.0 —— 我们**只借鉴了设计思路（egocentric 语义网格），没有取用任何代码**，所以它不传染。这一点已写在 changelog 里作为记录。

**你要决定的**：

| 选项 | 含义 | 适合 |
|---|---|---|
| 不开源（保留所有权利 + 明确声明） | 现状合法化：写明源码可读但不授权使用 | 想以后商业化、或还没想好 |
| MIT / Apache-2.0 | 任何人可用可改可闭源分发；Apache-2.0 另含专利授权与商标条款 | 想要贡献者和生态 |
| AGPL-3.0 | 强 copyleft，网络分发也要开源 | 想防止别人闭源二次分发 |

**我的建议**：如果 1.0 的目标是**发布一个装得上的桌面应用**，而不是建立开源社区，那先写「保留所有权利」的明确声明，把开源决定推到 1.0 之后 —— 因为许可证一旦公开发布就很难收回，而现在的模糊状态对谁都没好处。

**告诉我你选哪个**，我来做机械部分：写 LICENSE、在 `package.json` / `Cargo.toml` / `tauri.conf.json` 补 license 字段、README 加声明、按 lockfile 重新生成完整的传递依赖第三方通知（含许可证全文）。

---

## 步骤 2：Apple 签名与公证（首次 1–2 小时）

没有这一步，用户下载的包会被 Gatekeeper 拦住，只能右键打开——那不是一个能发布的产品。

### 2.1 加入 Apple Developer Program

- https://developer.apple.com/programs/ ，个人或公司，约 **$99/年**。
- 审核通常几小时到两天。**这是唯一有等待时间的一步，建议今天就开始。**

### 2.2 创建 Developer ID Application 证书

最省事的路径是 Xcode：

1. Xcode → Settings → Accounts → 登录你的 Apple ID → 选中 Team → **Manage Certificates…**
2. 左下角 **+** → **Developer ID Application**
3. 关掉窗口，证书已经在你的 login keychain 里

确认它在：

```bash
security find-identity -v -p codesigning
```

输出里应该有一行形如 `Developer ID Application: 你的名字 (ABCDE12345)` —— **整个引号内的字符串**就是 `APPLE_SIGNING_IDENTITY`，末尾括号里的 10 位就是 `APPLE_TEAM_ID`。

### 2.3 导出 .p12

1. 打开 **钥匙串访问** → 「登录」钥匙串 → 「我的证书」
2. 找到 `Developer ID Application: …`，右键 → **导出**
3. 存为 `joi-signing.p12`，**设一个导出密码**（这就是 `APPLE_CERTIFICATE_PASSWORD`）

转成 base64（GitHub secret 只能存文本）：

```bash
base64 -i ~/Desktop/joi-signing.p12 -o ~/Desktop/joi-signing.p12.base64
```

### 2.4 生成 App-Specific Password

公证用的不是你的 Apple ID 密码：

1. https://appleid.apple.com → 登录 → **登录与安全** → **App 专用密码**
2. 生成一个，命名 `joi-notarization`
3. 记下形如 `abcd-efgh-ijkl-mnop` 的字符串（**只显示一次**）—— 这是 `APPLE_PASSWORD`

### 2.5 写入 GitHub secrets

`gh` 已经登录 `Gallo233`，直接在仓库目录里跑：

```bash
gh secret set APPLE_CERTIFICATE < ~/Desktop/joi-signing.p12.base64
```

```bash
gh secret set APPLE_CERTIFICATE_PASSWORD
```

```bash
gh secret set APPLE_SIGNING_IDENTITY
```

```bash
gh secret set APPLE_ID
```

```bash
gh secret set APPLE_PASSWORD
```

```bash
gh secret set APPLE_TEAM_ID
```

> 后五条不带 `<` 会进入交互式输入，粘贴后回车 —— 这样密码不会留在 shell 历史里。**不要**用 `--body "密码"` 的写法。
>
> `APPLE_ID` 填你的 Apple 账号邮箱；`APPLE_PASSWORD` 填 2.4 的 app 专用密码，**不是**账号密码。

写完删掉本地导出的文件：

```bash
rm -f ~/Desktop/joi-signing.p12 ~/Desktop/joi-signing.p12.base64
```

核对（只列名字，不显示值）：

```bash
gh secret list
```

**完成后我能接手**：workflow 里任何签名/公证相关的失败排查。

---

## 步骤 3：打包并托管授权资产（30 分钟）

release 构建是 fail-closed 的：拿不到完整 Live2D 资产就不出包。资产**不能**为了让 CI 通过而提交进这个公开仓库（现在也确实没有，已确认 `public/live2d/` 与 `public/vendor/live2d/` 都是 gitignored）。

### 3.1 打包

我已经核对过：**你本机 `agent_companion/shell/public/` 下的 7 个文件全部与 `release-assets.json` 的固定哈希一致**，可以直接打包。

```bash
cd "agent_companion/shell" && rm -f public/.DS_Store && ditto -c -k --sequesterRsrc --keepParent public ~/Desktop/joi-release-assets.zip
```

自检（解出来必须有一个 `public/` 目录）：

```bash
rm -rf /tmp/joi-assets-check && mkdir -p /tmp/joi-assets-check && ditto -x -k ~/Desktop/joi-release-assets.zip /tmp/joi-assets-check && ls /tmp/joi-assets-check/public
```

### 3.2 托管

workflow 用的是**不带认证头的 `curl`**，所以这个 URL 必须自己带鉴权或不可猜：

- **推荐**：Cloudflare R2 / AWS S3 的**预签名 URL**（有效期设长一点，比如 1 年），或私有 bucket + 长随机路径。
- **不要**用需要登录才能下载的网盘分享链（curl 拿到的会是 HTML 登录页，构建会在哈希校验处失败）。
- URL 本身就是凭据，所以它进 secret 而不是进文档。

### 3.3 算哈希并写入 secrets

```bash
shasum -a 256 ~/Desktop/joi-release-assets.zip
```

```bash
gh secret set JOI_RELEASE_ASSETS_URL
```

```bash
gh secret set JOI_RELEASE_ASSETS_SHA256
```

> 注意：**以后任何一次改动角色资产，都要重新打包、重新上传、重新写这两个 secret**，否则构建会在哈希校验处直接失败（这是设计如此）。

---

## 步骤 4：跑第一次 draft release（20 分钟 + 构建等待）

前置：步骤 0、2、3 都完成。

```bash
gh workflow run "Joi macOS Draft Release"
```

```bash
gh run watch
```

流程会做：跑全部门禁 → 拉取并校验授权资产 → 构建桥接 → 构建 Core sidecar → 签名 → 公证 → 传到 draft prerelease → 记录 build-provenance attestation。

**预期的第一次失败点，按概率排序**：

1. 公证被拒 —— 多半是 `APPLE_PASSWORD` 填成了账号密码而不是 app 专用密码。
2. 资产 URL 取回的不是 ZIP —— 见 3.2 的网盘陷阱。
3. 桥接 `npm ci` 拉不到包 —— 重跑一次通常就好。

拿到产物后：

```bash
gh release list
```

```bash
gh release download <tag> --pattern "*.dmg" --dir ~/Downloads
```

**这一步跑通本身就是 Phase 5 的一项证据**：仓库第一次产出签名且公证过的产物。

---

## 步骤 5：干净机验收（2–3 小时，一次跑完能消四项）

**需要一台没有 Python / Node / Rust / 仓库 checkout / Joi 配置的 Apple Silicon Mac。** 不能用你的开发机 —— 开发机上装着的东西会掩盖真实首次运行的问题。同事的机器、新账户、或抹掉重装的备用机都行；**新建一个 macOS 用户账户是最省事的近似**（但装过的系统级依赖仍在，属于打折的证据，记录时要写明）。

按 `docs/MACOS_RELEASE.md` 的五步走，下面是补充了「要看什么」的版本：

### 5.1 安装与首次运行

- 双击 DMG 安装 → 首次打开**不应**出现「无法验证开发者」。出现了就是公证没生效。
- 确认原生红绿灯窗口控件、角色渲染正常。
- 走一遍 Hero Journey：**基础配置 → 与默认角色的一次真实对话 → 进入项目 → 观察一个可信窗口 → Joi 提出一个非敏感动作 → 逐步确认 → 执行并重新观察验证 → 生成一条待确认记忆 → 你确认它**。

  > 这条链路是 PRD §5.2 冻结的首发主线，**它跑不通比任何 Beta 能力出问题都严重**。

- BYOK 配置、文件/文件夹附件、角色切换与卸载、记忆隔离、退出重开。

### 5.2 日志检查

```bash
ls ~/Library/Logs
```

打开 Joi 应用数据目录下的 `logs/`，确认里面**有**可用的启动错误信息，且**没有** API key、提示词原文、本地文件内容、session token。

### 5.3 权限撤销（Phase 5 独立一项）

系统设置 → 隐私与安全性 → 分别关掉 **辅助功能** 和 **屏幕录制**，然后让 Joi 去做需要它们的事：

- 应当**明确说出缺哪个权限**，并给出去哪里开的指引；
- **不应**反复重试、卡住、或者假装动作成功了。

### 5.4 多屏与缩放（Phase 5 独立一项）

接一台外接显示器，最好是和内置屏**不同缩放比例**的：

- 把窗口拖到副屏，让 Joi 做一次 Computer Use 点击；
- 确认点到的是**你看到的那个位置**。已知问题里记着副屏坐标偏移，这一步就是去证实或推翻它。

### 5.5 VoiceOver（Phase 5 独立一项）

`Cmd + F5` 打开 VoiceOver，用键盘走一遍主界面：

- 审批卡片能否被读出、能否被键盘操作 —— **这一条最关键**，因为审批是安全边界，不可达等于不可用；
- 聊天区、设置、角色切换能否到达。

### 5.6 升级与回滚演练（Phase 5 独立一项）

装一个更早的版本，再装新版本覆盖，确认：

- 配置与记忆还在；
- 数据库迁移没丢东西；
- 装回旧版本时不会崩（或者明确告诉用户不支持降级）。

**记录方式**：每一项写 pass/fail + 一句话，**不要**贴截图、路径、日志原文、账号信息。写完发我，我来更新 `docs/RELEASE_READINESS_2026-08-18.md` 与 `docs/KNOWN_ISSUES.md`。

---

## 步骤 6：Minecraft 真实服走查（2–3 小时，**不阻塞 1.0**）

PRD §5.2 把游戏适配器划在 Beta 轨道，所以这条**不影响发布**，但它是切片验收唯一还缺的证据 —— 24 个动作里只有最早的 10 个在真实世界跑过。

准备：HMCL 开一个 Java 版单人世界 → 「对局域网开放」→ 把屏幕上显示的端口填进 Joi 的 Minecraft Skill。世界里准备树、石头、箱子、工作台、熔炉、床、水域；生存模式、夜里能刷怪；角色包设成「胆小」类人设。

先跑离线门禁：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' && .venv/bin/python tools/minecraft_p5_smoke.py
```

初始化记录：

```bash
.venv/bin/python tools/minecraft_walkthrough_report.py --init
```

然后按 `docs/MINECRAFT_REAL_SERVER_WALKTHROUGH.md` 的 18 个场景逐个勾，每个记一条：

```bash
.venv/bin/python tools/minecraft_walkthrough_report.py --add combat_persona --status pass --category ok --note "怕的时候先跑，等指令才动手"
```

```bash
.venv/bin/python tools/minecraft_walkthrough_report.py --summary
```

**最该盯的三个场景**（其余都是功能验证，这三个是回退风险）：

- `route_budget` —— 把 `max_blocks_changed` 设成 8 再让她跨越地形，确认她在第 8 块停下并报 `block_budget_exhausted`，**世界不再被继续改**。这是我这轮刚补的账目，只有真实寻路能验证。
- `combat_persona` —— 胆小人设被打时**不能**主动攻击，要提议逃跑并问你；你说「打它」之后才允许。
- `coordinate_leak` —— 全程留意回执、字幕、语音、记忆里**有没有出现坐标**。出现即为隐私回退，优先级高于任何功能问题。

记录工具会拒绝像坐标、地址、端口的备注 —— 这是故意的，走查跑在你自己的世界里。

**完成后我能接手**：任何 fail 的修复，以及把结果写回切片文档 §8。

---

## 步骤 7：资产权利确认（时间取决于外部方）

这些我完全无法代劳，且**每一项都能单独卡住公开发布**：

| 项 | 你要确认什么 |
|---|---|
| 默认角色 Live2D 模型 | 谁画的、什么授权、能否随商业/免费应用分发 |
| **Live2D Cubism Expandable Application 审批** | Joi 支持用户导入角色包，这在 Live2D 条款里属于「可扩展应用」，需要向 Live2D 公司单独申请。**这一项有外部审批周期，越早提交越好。** |
| 应用图标 | 现在是 provisional 资产。定稿后要**同时**重新生成 `icon.png` / `icon.icns` / `icon.ico`（`agent_companion/shell/src-tauri/icons/`） |
| 字体 | 嵌入的字体是否允许随应用分发 |
| 角色声音 / GPT-SoVITS 参考音频 | 声音提供者的授权范围 |

---

## 一句话优先级

**今天就该开始的只有两件**：加入 Apple Developer Program（步骤 2.1，有审核等待）和提交 Live2D Expandable Application 审批（步骤 7，有外部周期）。其余的都能等这两件跑着的时候顺手做完。

**唯一在等你一句话的**：许可证选哪个（步骤 1）—— 你说一声，剩下的机械工作我今天就能做完。
