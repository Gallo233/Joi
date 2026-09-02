# 安装 Joi（macOS）

> 这份文档是写给下载者的。GitHub Release 的说明可以直接引用它。

## 系统要求

- Apple Silicon Mac（M 系列）
- macOS 12 或更新
- 一个你自己的模型 API key —— Joi 不代管密钥，也不内置额度

## 安装

1. 从 Release 页下载 `Joi_<版本>_aarch64.dmg`。
2. （可选，建议）核对下载完整性，输出应与 Release 页上的 SHA-256 一致：

   ```bash
   shasum -a 256 ~/Downloads/Joi_0.1.0_aarch64.dmg
   ```

3. 打开 DMG，把 Joi 拖进「应用程序」。

## 第一次打开：系统会拦一下，这是正常的

Joi 由个人开发者从 GitHub 分发，**没有经过 Apple 的公证（notarization）**。macOS 因此会在第一次打开时拦住它，提示大意是「Apple 无法验证此 App 是否包含恶意软件」。

这**不是**下载损坏。安装包本身是完整签名封装的，你可以用上面的 SHA-256 自己核对。

**macOS 15 (Sequoia) 及更新的系统**（包括 macOS 26）：

1. 双击 Joi，在提示框上点「完成」。
2. 打开 **系统设置 → 隐私与安全性**。
3. 下滑到「安全性」一节，会看到一行「已阻止使用 "Joi"」，点右边的 **仍要打开**。
4. 再次确认，输入你的登录密码。

> macOS 15 起，Apple 取消了「右键 → 打开」这条绕过路径，所以必须走系统设置这一步。之后每次打开都不会再问。

**macOS 14 及更早**：在「应用程序」里右键（或按住 Control 点按）Joi → **打开** → 在弹窗里再点一次「打开」。

**任何版本，一条命令解决**：

```bash
xattr -dr com.apple.quarantine /Applications/Joi.app
```

这条命令删掉的是「从网络下载」这个隔离标记。只对你确实信任来源的下载这么做。

## 首次运行会要什么权限

Joi 会在你**第一次用到相应能力时**才申请，全部可以拒绝：

| 权限 | 用来做什么 | 拒绝会怎样 |
|---|---|---|
| 辅助功能 | 代你点击、输入、操作窗口 | Joi 明说缺这个权限并告诉你去哪开，不会假装动作成功 |
| 屏幕录制 | 看你指定的窗口，作为她判断的依据 | 观察类能力不可用，会明确报告，不会猜 |
| 麦克风 | 你主动录音或开启实时语音会话时 | 语音输入不可用，文字对话不受影响 |

系统设置 → 隐私与安全性 里可以随时撤销；撤销后 Joi 会重新说明缺什么，而不是静默降级。

## 你的数据在哪

- 应用数据（项目、对话、记忆、角色、设置、日志）：`~/Library/Application Support/com.gallo233.joi`
- API key 存进系统钥匙串，配置文件里只留一个引用，不存明文
- 对话与记忆默认留在本机；发给模型供应商的只有那次请求需要的内容

完整口径见 `docs/PRIVACY.md`。

## 卸载

把 `/Applications/Joi.app` 拖进废纸篓，然后删掉数据目录：

```bash
rm -rf ~/Library/Application\ Support/com.gallo233.joi
```

## 遇到问题

Release 页上开 issue，附上 `~/Library/Application Support/com.gallo233.joi/logs/` 里的启动日志。日志里不会有 API key、提示词原文或本地文件内容 —— 这是设计如此；如果你发现有，那本身就是要报的 bug。
