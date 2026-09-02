# 吸收 AIRI 的舞台与模型导入设计

> 状态：设计评审。依据 2026-08-18 实读 `moeru-ai/airi` 主分支源码，不是二手材料。
> 关联：`docs/AIRI_ABSORPTION_PLAN.md`（游戏/语音/记忆那一轮）、`docs/THIRD_PARTY_NOTICES.md`。

## 0. 先回答那个问题：AIRI 能加 VRM 角色吗

**能，而且不止 VRM。** `packages/stage-ui/src/stores/display-models.ts` 里的格式枚举是：

```ts
enum DisplayModelFormat {
  Live2dZip, Live2dDirectory, VRM, SpineZip, TachieZip, PMXZip, PMXDirectory, PMD
}
```

Live2D、VRM、Spine、立绘（Tachie，静态图）、MMD（PMX/PMD）五类渲染器。预置模型四个：

```ts
{ name: 'Hiyori (Pro)',    format: Live2dZip, url: 'hiyori_pro_zh.zip' }
{ name: 'Hiyori (Free)',   format: Live2dZip, url: 'hiyori_free_zh.zip' }
{ name: 'AvatarSample_A',  format: VRM,       url: 'AvatarSample_A.vrm' }
{ name: 'AvatarSample_B',  format: VRM,       url: 'AvatarSample_B.vrm' }
```

两个 Live2D 来自 Live2D 官方示例，两个 VRM 来自 VRM Consortium 官方示例。**四个全部保留原名**——这就是我们这次改名依据的做法。

## 1. 他们的舞台设计

### 1.1 一个 Stage，多个渲染器包

```
packages/
  stage-ui/            ← Stage.vue：编排、情绪、口型、语音、字幕
  stage-ui-live2d/     ← Live2DScene
  stage-ui-three/      ← ThreeScene（VRM 走这里）
  stage-ui-spine/      ← SpineScene
  stage-ui-tachie/     ← TachieScene
  stage-ui-mmd/        ← MMDScene
```

`Stage.vue` 自己不认识任何一种模型格式。它持有的是**跨格式的共享契约**：

- 情绪：一套 `Emotion` 枚举 + 两张映射表 `EMOTION_EmotionMotionName_value`（Live2D 动作名）与 `EMOTION_VRMExpressionName_value`（VRM 表情名）；
- 口型：`@proj-airi/model-driver-lipsync`，一个独立的 driver 包；
- 语音：`createSpeechPipeline` / `createPlaybackManager` / `StageTtsSession`；
- 字幕：`CaptionChannelEvent`；
- 失败：`stage-render-error.vue` 是一个专门的渲染失败态组件。

**值得抄的是这条分界**：舞台负责「她现在该是什么情绪、该说什么、口型多大」，渲染器包负责「这个格式怎么把它演出来」。加一种新格式不需要动舞台。

### 1.2 Joi 现在的样子

Joi 的 `JoiCharacter.vue` 已经有 `'static' | 'live2d' | 'vrm' | 'procedural3d'` 四种，切换逻辑写在同一个组件里（`props.modelType === 'procedural3d' ? … : props.modelType === 'vrm' ? …`）。

规模还小的时候这样没问题。差别在于 AIRI 把「情绪 → 格式表现」的映射**外置成了表**，而 Joi 目前混在渲染分支里。**这一条建议吸收**：把 expression map 抽成 `emotion → {live2d: motion, vrm: expression, procedural: pose}` 的单张表，渲染器只查表。

## 2. 他们的模型导入设计（这一块比舞台更值得抄）

### 2.1 导入流程：校验 → 报告 → 确认 → 入库

`model-selector.vue` 的 Live2D 分支：

```
handleAddLive2DModel(file)
  → validateLive2DZip(file)        // 只读校验，不落盘
  → validationReport               // 结构化报告
  → showReportModal                // 给用户看
  → confirmImport()                // 用户确认后才 addDisplayModel
```

**这个形状 Joi 已经有了**——Skill 安装就是 inspect → preview → digest 审批 → install。但**角色包导入没有**。同一套 UI 语法用在两个地方，用户不用学第二遍。

### 2.2 校验报告里到底检查什么

`live2d-validator.ts` 的 `Live2DValidationReport { errors[], warnings[] }`，这些检查项是真实世界踩出来的，不是想象的：

| 检查 | 触发的现实问题 |
|---|---|
| 没有 `.model3.json` 但有 N 个 `.moc3` | 结构不合法，说清楚数量 |
| MOC3 文件头不是 `MOC3` | 拿到的根本不是模型 |
| `.moc3` 体积超阈值 | **CRITICAL**：超出浏览器 WASM 内存，会直接崩；较低阈值给 warning |
| **basename 冲突** | 同名文件在不同目录 → 加载器丢数据 |
| **大小写不匹配** | `model3.json` 写 `Texture.png`，包里是 `texture.png`；浏览器区分大小写 |
| 引用的文件不存在 | 报出「期望在哪」而不是只说缺失 |
| JSON 解析失败 | 带上具体错误 |

外加两条工程细节：

- **legacy 编码文件名**：VTube Studio 导出的 zip 常常不带 UTF-8 标志位，中日文文件名会变乱码。他们写了 `decodeZipFileName`，并且**校验器必须用同一个解码器**，否则校验看到的是乱码路径、会把好包判死。
- **`__MACOSX` 与 `._` 前缀条目直接忽略**——macOS 打 zip 的产物。

> 这两条 Joi 立刻能用：我们自己写发布资产打包步骤时就专门叮嘱了 `--sequesterRsrc`，同一个坑。

### 2.3 降级：没有设置文件也能加载

`ZipLoader.createSettings` 找不到 `.model3.json` 时走 `createFakeSettings(filePaths)`——用户丢进来一个裸 `.moc3` 也能显示，而不是报错拒绝。

同时它会额外抽取：`.cdi3.json`（参数显示名）与所有 `.exp3.json`（表情文件），挂在 settings 上给上层用。

### 2.4 缩略图是渲染出来的

每种格式一个 `generate*Preview(file)`，**真的把模型渲染一帧**当预览图，存进模型库。所以模型选择器是一排缩略图而不是一排文件名。

### 2.5 存储

- 预置模型：内存常量，`type: 'url'`
- 用户导入：IndexedDB（localforage），key 为 `display-model-<nanoid>`，`type: 'file'`
- 两者合并后按 `importedAt` 倒序

有一处注释值得单独看：`getDisplayModel` 先读内存再读 IndexedDB，因为「刚导入的模型还没写完 IndexedDB，直接读会 miss 然后 fallback 到默认模型」。这是并发 bug 修完留下的记录——和 Joi 代码里的注释风格一致。

## 3. Joi 该吸收什么、不该吸收什么

### 建议吸收

1. **角色包导入的「校验 → 报告 → 确认」**：复用 Skill 安装那套 UI 语法，报告内容抄 §2.2 的检查表。这是最高性价比的一条：现在 Joi 导入一个坏包，用户只能看到加载失败。
2. **zip 现实世界加固**：legacy 编码文件名、`__MACOSX`/`._`、basename 冲突、大小写不匹配、MOC3 文件头与体积阈值。每一条都是真实用户会遇到的。
3. **情绪映射外置成表**：`emotion → {live2d, vrm, procedural3d}`，渲染器查表。
4. **模型库带缩略图**：预置与导入合并成一个列表，缩略图由渲染一帧生成。
5. **裸模型降级**：没有 settings 文件时用推断设置加载，而不是拒绝。

### 不建议吸收

- **IndexedDB 存模型**：AIRI 是 Web-first 所以只能这样。Joi 是桌面应用，角色包已经有落盘目录、manifest、provenance 和哈希校验，比浏览器存储强。
- **格式全家桶（Spine / MMD / 立绘）**：每种都是一个渲染器包加一套校验加一套预览。1.0 不需要；PRD §5.3 也写了不承诺。
- **他们的信任模型**：AIRI 的导入是「校验能不能加载」，Joi 的是「校验能不能信任」——digest 审批、provenance 实测记录、拒绝携带聊天记录/记忆/权限的角色包。**这一层 Joi 明显更强，不能为了对齐 UI 而弱化。**

正确的合并方式是：**AIRI 的可用性检查报告，接在 Joi 的信任检查后面**，成为同一张卡片上的两段——「这个包能不能加载」和「这个包要不要信任」。

## 4. 与本轮改名的关系

这次按 AIRI 的做法把名字改回来了：

- 默认角色的**人格**叫 Joi（应用名即角色名，与 AIRI 一致：AI 叫 AIRI，模型叫 Hiyori）；
- 默认角色的**形象**是 Live2D 官方示例 **Hiyori Momose（桃瀬ひより）**，文件名 `hiyori.*`，署名 © Live2D Inc.，写进 `default_character.yaml` 的 `artwork` 段与第三方通知；
- 「星野澪」已删除。

依据是 Live2D 示例数据条款的三条硬性要求：保留版权声明、不改动角色设计、不得作为发布者的原创角色呈现。AIRI 冒的风险只有 Cubism Core 的 Expandable Application 那一项，角色署名这一项他们是合规的——因为合规不要钱。
