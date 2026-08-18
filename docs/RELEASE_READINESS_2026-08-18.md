# 1.0 发布就绪状态 — 2026-08-18

> 状态：证据审计。逐项标注**证据来源**，不接受「代码里有」当作证据。
> 判据：`docs/JOI_PRD.md` §5.2 首发 Hero Journey 与 §18 验收标准、`docs/JOI_TDD.md` §17 Phase 5 退出条件、`docs/ROADMAP.md` 发布阻塞项。
> 边界提醒：PRD §5.2 明确 **Watch/Scene、第三方 Skill 安装、游戏适配器、完整角色 CRUD、Coding Agent takeover 不阻塞首发**。B1 轨道（Minecraft + 实时语音）做得再厚也不会让 1.0 更近一步。

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
