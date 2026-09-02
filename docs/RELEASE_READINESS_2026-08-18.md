# 1.0 发布就绪状态 — 2026-08-18

> 状态：证据审计。逐项标注**证据来源**，不接受「代码里有」当作证据。
> 判据：`docs/JOI_PRD.md` §5.2 首发 Hero Journey 与 §18 验收标准、`docs/JOI_TDD.md` §17 Phase 5 退出条件、`docs/ROADMAP.md` 发布阻塞项。
> 边界提醒：PRD §5.2 明确 **Watch/Scene、第三方 Skill 安装、游戏适配器、完整角色 CRUD、Coding Agent takeover 不阻塞首发**。B1 轨道（Minecraft + 实时语音）做得再厚也不会让 1.0 更近一步。

## 2026-09-02 更新：构建路径已验证，发布路径未验证

在 `minecraft-slice@4f107f7` 上把所有能在本机跑的门禁跑了一遍，并**第一次产出了完整的 release 包**。结论一句话：能构建，不能发布。

### 本机实测（2026-09-02，Apple Silicon / Xcode 26.6 / Node 22.22.3 / Rust 1.95）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 单元/契约/迁移/恢复测试 | `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` | **844 通过**（8-18 是 734；含签名验证的 6 项） |
| Core 回归套件 | `.venv/bin/python run_agent_companion_tests.py` | 通过 |
| Shell 测试 | `npm run test:shell` | **128 通过**（8-18 是 94） |
| 打包元数据与隐私扫描 | `.venv/bin/python tools/packaging_smoke.py` | 48 项全 OK，0 warn 0 fail |
| 固定资产校验 | `npm run assets:verify` | 7 项 pinned 资产通过 |
| 第三方通知 | `tools/generate_third_party_notices.py --check` | up to date（619 条） |
| **macOS release 构建** | `npm run tauri -- build --target aarch64-apple-darwin` | **通过**，产出 `Joi.app`（600MB）与 `Joi_0.1.0_aarch64.dmg`（89.9MB） |
| 独立 Core sidecar 冒烟 | `.venv/bin/python tools/smoke_core_sidecar.py` | OK，冷启 23.1s / 热启 0.5–0.6s |

Info.plist 实测：`com.gallo233.joi` / `0.1.0` / 最低 macOS 12.0 / 三条中文权限说明在位。Minecraft 桥接的 `dist/` 也确实进了 sidecar。

### 这次构建暴露的两件事

**签名状态比"缺 secrets"更具体。** 本机 0 个签名身份，Tauri 于是跳过签名，留下链接器的 ad-hoc 签名：`Signature=adhoc`、`Sealed Resources=none`、`Identifier=joi_shell-1ac5d3d068a30b18`（连 bundle id 都不是 `com.gallo233.joi`），`spctl -a` 直接拒绝。也就是说本机产物只能在本机跑。`gh secret list` 为空 —— 8 个 secret 一个都没配，release workflow 会在第一步 fail-closed 退出。

**冷启动是一个真实的性能风险。** TDD §13 要求「冷启动到可交互 p95 ≤ 10s（含 sidecar ready）」。sidecar 首次执行 23.1s，之后 0.5–0.6s —— 差别是 600MB onedir 的页缓存。开发机上第二次之后永远看不到这个数，而**用户的第一次启动正是冷缓存**。这条只能在干净机上量，已写进 `docs/RELEASE_HANDS_ON.md` 步骤 5.1。

### 一处修好的绊脚石

`sync-live2d-assets.mjs` 的默认候选路径 `~/Documents/All Joi/public` 早已不含 hiyori（资产随站点布局搬到了 `public/joi-shell/`），所以 `docs/MACOS_RELEASE.md` 里那条本地构建命令在这台机器上必然 fail-closed 报错，除非手动带 `JOI_LIVE2D_SOURCE`。候选列表补上了 `joi-shell/` 子目录，现在不带环境变量也能解析。

### 清单收口：只剩干净机走查（当天最后一轮）

分发模型定下之后，清单上除干净机验收以外的每一项都做完了。

| 原阻塞项 | 结果 |
|---|---|
| 出包与签名 | `tools/build_macos_release.py` 跑通，ad-hoc 签名封装完整，`codesign --verify --deep --strict` 通过 |
| 第一个 GitHub Release | draft 已建，DMG 已上传，说明用 `docs/INSTALL_MACOS.md`，停在 draft 等走查 |
| 版本号与 tag | `v0.1.0`；CHANGELOG 的 `Unreleased` 收成 `0.1.0 — 2026-09-02`；`KNOWN_ISSUES` 里那批 `(v0.2.0)` 标签是历史遗留，已去掉 |
| 隐私声明 | 去掉 Draft。原文是一份规格（发布前**必须**做到什么），读起来却像对一个没人验过的构建的承诺；改写成这个构建**做什么**，并写明它够不到的两件事：供应商收到之后留了什么，以及 Time Machine 备份里的副本。无遥测/无崩溃上报是实测结论，不是意图 |
| 安全报告渠道 | 新增 `SECURITY.md` |
| 应用内版权声明 | 设置新增「关于」页；`packaging_smoke` 加 `legal_notice_sync` 与 `about_panel_notices` 两道门禁，Shell 测试加 4 项 |
| 字体与声音分发权 | **审计后确认不存在这个问题**：`.app` 里没有任何字体文件（界面用系统字体栈），也没有任何音频文件（GPT-SoVITS 参考音频由用户提供，本机 `data/` 下的是运行时缓存） |

**门禁复跑**：单测 844 通过、Shell 测试 132 通过、`packaging_smoke` 50 项全 OK、第三方通知 619 条 up to date、sidecar 冒烟 OK。

**这一轮做对的一件小事**：第三方通知现在随包分发，所以它里面那句「见 `docs/RELEASE_HANDS_ON.md`」和「需要与 Live2D 单独签约」被改掉了 —— 前者对下载者毫无意义，后者是把内部待办印在了用户看的文件上。通知只说权利事实；决定与残留风险留在 `KNOWN_ISSUES`。

---

### 分发模型已定（当天晚些时候）

**从 GitHub Releases 分发，不上 App Store，不做 Apple 公证；Live2D Expandable Application 不申请，按同类项目的做法照常发布。** 这是发布方的决定，两条最长的外部等待因此从清单上消失：Apple Developer Program 的审核与年费，以及 Live2D 的单独签约周期。

决定之后补的工程改动，只有一处，但它是必须的：**构建现在显式签名**。给不出身份时 Tauri 会跳过签名，留下链接器打在主二进制上的那个 ad-hoc 签名 —— 没有封装资源，代码标识是 `joi_shell-1ac5d3d068a30b18` 而不是 `com.gallo233.joi`。macOS 把这种包报成「已损坏」，用户看到的是下载坏了，而不是一个可以放行的安装步骤。带上 `APPLE_SIGNING_IDENTITY=-` 重建后：

| 项 | 之前 | 现在 |
|---|---|---|
| 代码标识 | `joi_shell-1ac5d3d068a30b18` | `com.gallo233.joi` |
| 封装资源 | `Sealed Resources=none` | `version=2 rules=13 files=1379` |
| Hardened runtime | 无 | `flags=0x10002(adhoc,runtime)` |
| `codesign --verify --deep --strict` | 未通过 | valid on disk, satisfies its Designated Requirement |
| `spctl -a` | 拒绝 | 拒绝（未公证，按设计如此） |

`tools/build_macos_release.py` 把这条路固定下来：挑可用的最强身份（有 Developer ID 就用，没有就 ad-hoc）、跑门禁、构建，然后把签名读回产物验证，封装缺失就失败。`tests/test_macos_release_build.py` 6 项覆盖身份选择与签名判读。release workflow 不再强制 6 个 Apple secrets，缺了就 ad-hoc 并在日志里 warning。

**这个决定新暴露的一项**：不申请 Expandable Application 之后，Live2D 示例数据条款要求的版权声明反而更要紧，而它现在**只存在于仓库**——应用里没有任何界面显示「桃瀬ひより © Live2D Inc.」。已记入 `docs/KNOWN_ISSUES.md` 与 `docs/RELEASE_HANDS_ON.md` 步骤 5。

### 阻塞项状态变化

8-18 清单里的四项已消：许可证、第三方通知、图标、资产哈希固定。**剩下的全部需要你**：Apple Developer 账号与证书、8 个 secrets、授权资产托管、第一个签名公证产物、干净机走查、隐私声明定稿、资产权利与 Live2D 审批、版本号决定。逐项教程见 `docs/RELEASE_HANDS_ON.md`。

---

## 2026-08-20 更新

这份审计写于 8-18，之后有三项落地、一项判错。

**已落地**（不再是阻塞项）：

| 项 | 证据 |
|---|---|
| 软件许可证 | 仓库有 `LICENSE`：保留所有权利，明确授予阅读与引用 |
| 第三方权利通知 | 按 lockfile 生成，`tools/generate_third_party_notices.py --check` 报「up to date (619 entries)」 |
| 应用图标 | 已换成定稿资产（commit 5a51597） |

**判错的一项**：上表把「长期数据的来源/作用域/查看/删除」记成 ✅，依据是 memory 的 scope/deletion 测试。那些测试是对的，但它们只覆盖 memory。会话删除另有一条路径，而它漏了：

- `delete_thread` / `delete_project` 只删 SQLite。`data/agent_companion/events.jsonl` 保留该会话的每一张 display card 和每一句 voice line，永久。用户确认过的删除，实际只是隐藏。
- 同一个文件从不回收。本机已长到 90MB，而读它的代码最多只看最后 400 行。

已修：删除现在同时清理该日志（含被删项目连带的 thread、以及旧到只带 thread id 的行），日志只保留有界尾部，且清理不会让重启后的 sequence 倒退。回归见 `tests/test_event_bus.py`（13 项，其中 3 项覆盖 RPC 接线本身——漏的正是接线）。

**新记入 `docs/KNOWN_ISSUES.md`**：`run_agent_companion_tests.py` 以仓库根目录为 workspace，所以文档里那条 Core 回归命令会往真实数据目录写事件——90MB 就是这么来的；`codex_runs/` 无上限；迁移前的 `.pre-sqlite-backup` 同样不受删除影响。

**顺带得到的证据**：控制面 RPC 在本机实测 `core.ping` 0.3ms、`runtime.status` 7.1ms、`conversation.history` 7.3ms、`audit.recent` 14.4ms，对 TDD §13 的 p95 ≤ 100ms 有很大余量。这是单次往返、非 p95，不能替代基准，但足以说明该项不是风险。

下面是 8-18 原文。

---

## 本机实测（2026-08-18）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 单元/契约/迁移/恢复测试 | `.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` | 734 通过 |
| Core 回归套件 | `.venv/bin/python run_agent_companion_tests.py` | 通过 |
| Shell 测试 | `npm run test:shell` | 94 通过 |
| Shell 类型检查与构建 | `npx vue-tsc --noEmit && npm run build` | 通过 |
| Tauri 壳 | `cargo check` | 通过 |
| 打包元数据与隐私扫描 | `.venv/bin/python tools/packaging_smoke.py` | 21 项全 OK |
| Minecraft 离线烟测 | `.venv/bin/python tools/minecraft_p5_smoke.py` | 22 动作 + 预算上限 + no-replay 通过 |
| Provider 预检 | `.venv/bin/python tools/provider_preflight.py` | WARN：ASR/OCR/vision/voice_style/system_audio 本机未配置 |
| MVP 演示检查 | `.venv/bin/python tools/mvp_demo_check.py` | WARN：Watch 需 vision/OCR，Codex 与 OK-WW 需本地安装 |

Provider 与演示的 WARN 是**本机配置**状态，不是产品缺陷：它们证明的是「未配置时降级正确」，这条本身就是 PRD §18 的验收项之一。

## 本轮补上的门禁缺口

审计中发现三处「以为在跑、其实没跑」：

1. **Shell 测试从未进 CI**。macOS required lane 只跑 `npm run build`（类型检查 + 打包），94 个 Shell 测试是纯本地的，不拦任何 PR。已加入两条 lane。
2. **CI 用 Node 20**，而 `test:shell` 依赖 `--experimental-strip-types`（Node 22.6+），bridge 打包脚本明确要求 Node 22+。已统一为 22。
3. **Minecraft 桥接在 CI 里只做 `node --check`**，从不构建 `dist/`，也就从不运行离线烟测；而 release 工作流同样不构建它——`dist/` 是 gitignored，所以**发布包里的游戏适配器永远处于不可用状态**（Core 是 fail-closed，会如实报告不可用，不会误动作）。已在 CI 与 release 两条流水线加入 `npm ci && npm run build`，并把离线烟测加入门禁；`packaging_smoke` 新增 `game_adapter_bundle` 检查，缺产物时 WARN 而不是静默。

## Phase 5 退出条件逐项

TDD §17 Phase 5 = 性能/可访问性/安全/干净机矩阵 + 许可证/隐私/第三方资产/签名/公证 + signed updater/备份/升级回滚演练。

| 项 | 状态 | 证据或缺口 |
|---|---|---|
| 自动化门禁（测试/构建/打包/隐私扫描） | ✅ | 上表；CI required lane 现覆盖 Python、Shell、Rust、sidecar、打包、桥接 |
| 未配置降级（模型/视觉/语音/资产） | ✅ | provider preflight + mvp demo check；PRD §18 要求的降级路径有测试 |
| 敏感动作授权/暂停/验证/审计 | ✅ | policy + 审批指纹 + 持久审计 + ActionReceipt，测试覆盖 |
| 长期数据的来源/作用域/查看/删除 | ✅ | memory scope/deletion 测试；删除后 recall=0 有断言 |
| **干净机验收** | ❌ | 需要一台没有 Python/Node/Rust/仓库/配置的 Apple Silicon Mac，按 `docs/MACOS_RELEASE.md` 五步走查 |
| **签名与公证真机证据** | ❌ | workflow 就绪，缺 6 个 Apple secrets，从未产出已签名产物 |
| **多屏 Retina 基准** | ❌ | 已知问题里仍记着副屏坐标偏移，无真机数据 |
| **VoiceOver / 可访问性** | ❌ | 无证据 |
| **权限撤销走查** | ❌ | 撤销 Accessibility / Screen Recording 后的行为未在真机验证 |
| **升级与回滚演练** | ❌ | 无 signed updater 演练记录 |
| **性能基准** | ❌ | TDD §13 有预算，无实测 |

## 发布阻塞项（工程之外）

这些不是代码问题，必须由你决定或提供：

| 阻塞项 | 现状 | 需要什么 |
|---|---|---|
| 软件许可证 | 仓库无 LICENSE | 选一个许可证。这决定第三方通知的兼容性检查怎么做，应当**最先定**。 |
| 隐私声明 | `docs/PRIVACY.md` 是 Draft，且落后于实际能力 | 文本已按当前行为补全（实时语音、游戏、屏幕证据、时延埋点），仍需你确认口径并去掉 Draft 标记 |
| 第三方权利通知 | `docs/THIRD_PARTY_NOTICES.md` 只列直接依赖 | 需要按 lockfile 生成完整传递依赖清单并附许可证全文；PyInstaller 的 GPL-2.0 bootloader 例外条款要单独确认 |
| 角色/图标/字体/声音/Live2D 权利 | 待确认 | 每一项资产的分发权；Live2D Cubism Expandable Application 审批 |
| 应用图标 | provisional 资产 | 定稿后重新生成 PNG/ICNS/ICO |
| Apple 签名与公证 | workflow 就绪 | 6 个 GitHub secrets |
| 授权资产分发 | 未配置 | `JOI_RELEASE_ASSETS_URL` 与 `JOI_RELEASE_ASSETS_SHA256`；授权资产不得为过 CI 提交进仓库 |

## 建议顺序

1. **定许可证**——它是第三方通知与资产权利工作的前置。
2. **第三方通知按 lockfile 重新生成**——纯机械工作，定了许可证就能做完。
3. **配 Apple secrets 跑一次 draft release**——第一次产出已签名公证的产物，本身就是证据。
4. **干净机走查**——`docs/MACOS_RELEASE.md` 的五步，一次跑完就能同时消掉干净机、权限撤销、多屏与 VoiceOver 四项里的大部分。
5. 剩下的性能基准与升级回滚演练，可在候选阶段补。

在第 3 步之前，GitHub release 只能停留在 draft/prerelease。

需要你亲自动手的每一步（Apple 证书、授权资产打包托管、干净机走查、资产权利）写在 `docs/RELEASE_HANDS_ON.md`。
