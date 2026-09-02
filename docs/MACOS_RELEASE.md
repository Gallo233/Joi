# Joi macOS Release Guide

Joi 的首个公开包面向 Apple Silicon macOS。应用包内含 Vue/Tauri 壳与独立的 `joi-core` sidecar；下载者不需要这个仓库，也不需要 Python、npm 或 Rust。

## 分发模型（已定）

**从 GitHub Releases 分发，不上 App Store，不做 Apple 公证。** 下载者第一次打开时由系统设置放行，步骤写在 `docs/INSTALL_MACOS.md`，Release 说明必须指向它。

这条路省掉的是 Apple Developer Program（$99/年）与公证流水线，换来的是每个用户多两次点击。**没有省掉的是签名本身**：给不出任何身份时，Tauri 会跳过签名，留下链接器打在主二进制上的那一个 ad-hoc 签名 —— 没有封装资源，代码标识甚至不是 bundle id。macOS 会把这种包报成「已损坏」，而不是「未验证」，用户看到的是下载坏了，不是一个安装步骤。所以构建始终显式签名：机器上有 Developer ID 就用它，没有就 ad-hoc。

## 本地出包（主路径）

授权资产在你机器上，所以本地构建不需要任何 secret：

```bash
.venv/bin/python tools/build_macos_release.py
```

它会挑选可用的最强签名身份、跑 fail-closed 的发布门禁（构建独立 Core、要求完整 Live2D 资产、校验固定哈希）、构建并签名，然后**把签名读回来验证**：代码标识必须是 `com.gallo233.joi`，资源必须封装。最后打印 DMG 路径、体积与 SHA-256。

资产不在默认位置时：

```bash
.venv/bin/python tools/build_macos_release.py --live2d-source /absolute/path/to/public
```

只想复验已有产物，加 `--verify-only`。

发布：

```bash
gh release create v0.1.0 --draft --prerelease --title "Joi v0.1.0" --notes-file docs/INSTALL_MACOS.md agent_companion/shell/src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/Joi_0.1.0_aarch64.dmg
```

把上一步打印的 SHA-256 写进 Release 说明，下载者才能核对完整性。

## CI 出包（可选）

`.github/workflows/release-macos.yml` 走同一套门禁，另外记录 build-provenance attestation。它需要两个 secret，因为 runner 上没有授权资产：

- `JOI_RELEASE_ASSETS_URL`：一个私有 HTTPS 直链，ZIP 根目录含 `public/`。workflow 用不带认证头的 `curl` 取它，所以 URL 必须自带鉴权或不可猜。
- `JOI_RELEASE_ASSETS_SHA256`：该 ZIP 的 SHA-256。

`APPLE_*` 六个 secret 是**可选**的：不配就 ad-hoc 签名并在日志里 warning，配了就用 Developer ID 签名，六个都配齐才会公证。授权资产不得为了让 CI 通过而提交进仓库；ZIP 必须包含 `agent_companion/shell/release-assets.json` 列出的全部 Live2D 模型与运行时文件。

## 发布前检查

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
.venv/bin/python run_agent_companion_tests.py
npm run test:shell --prefix agent_companion/shell
.venv/bin/python tools/packaging_smoke.py
.venv/bin/python tools/smoke_core_sidecar.py
```

然后在一台**没有 Python / Node / Rust / 仓库 checkout / Joi 配置**的 Apple Silicon Mac 上：

1. 装 DMG，按 `docs/INSTALL_MACOS.md` 走一次放行流程 —— **这一步现在是验收项**：文档里写的点击路径必须和系统实际弹出的一致。
2. 验证启动、原生窗口控件、角色渲染、一次本地对话、BYOK 配置、文件/文件夹附件、角色切换与卸载、记忆隔离、退出重开。
3. 确认 `~/Library/Logs` 与应用数据目录下的 `logs/` 有可用的启动错误，且没有 API key、提示词原文、本地文件内容或 session token。
4. 撤销辅助功能与屏幕录制，确认 Joi 明说缺哪个权限，不循环重试也不假装成功。
5. 记录从双击到可交互的秒数（TDD §13 预算 p95 ≤ 10s，首次启动是冷缓存，这是唯一能量到真实值的机会）。

## 权利与通知

- **Live2D Cubism Core 是专有软件**，不受本仓库 LICENSE 覆盖，其再分发由 Live2D 自己的 SDK 许可证管辖。
- **默认角色是 Live2D 官方示例模型「桃瀬ひより / Hiyori Momose」，版权归 Live2D Inc.**。条款要求保留版权声明、不得改动角色设计、不得作为发布者的原创角色呈现 —— 应用里她就叫 Hiyori，人格叫 Joi，两者分开。
- **用户可导入自己 Live2D 模型的应用属于 Expandable Application**，Live2D 条款要求单独签约。**已决定不申请，按 AIRI 的做法照常发布**；残留风险与它的边界记在 `docs/KNOWN_ISSUES.md`。
- 字体、角色声音与 GPT-SoVITS 参考音频的分发权仍需逐项确认。
- 第三方通知按 lockfile 生成：`.venv/bin/python tools/generate_third_party_notices.py --check`。

保持 GitHub release 处于 draft/prerelease，直到干净机走查跑完。
