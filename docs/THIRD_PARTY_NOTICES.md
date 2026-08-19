# 第三方通知 / Third-Party Notices

> **本文件由 `tools/generate_third_party_notices.py` 生成，不要手工编辑。**
> 依赖一变它就过期，而过期的通知比没有更糟——它是一份关于「包里到底装了什么」的声明。
> 每次发布前重新生成：`.venv/bin/python tools/generate_third_party_notices.py`

Joi 自身的代码按仓库根目录的 `LICENSE` 授权（保留所有权利，非开源）。
**本文件列出的组件不受该 LICENSE 约束**，它们各自由其作者按下列许可证授权。

当前共 619 项。

## 覆盖范围与边界

- **npm**：Shell 与 Minecraft 桥接的**生产依赖树**。构建工具（Vite、TypeScript、Tauri CLI、ncc）不随发布物分发，因此不在此列。
- **Python**：PyInstaller 冻结进 Core sidecar 的发行版，读自构建环境。
- **Rust**：Tauri 壳解析出的 crate 图。
- **资产与内联代码**：没有 lockfile 能描述它们，逐项人工确认。

## 需要单独注意的三项

1. **Live2D Cubism Core** 是专有软件，不是开源件。仓库的 LICENSE 完全不适用于它，它的再分发由 Live2D 自己的 SDK 许可证管辖；而**允许用户导入自己的 Live2D 模型的应用属于 Expandable Application**，需要与 Live2D 单独签约。见 `docs/RELEASE_HANDS_ON.md`。
2. **默认角色的 Live2D 形象是 Live2D 官方示例模型「桃瀬ひより / Hiyori Momose」，版权归 Live2D Inc.**。示例数据条款允许 General User 与小规模企业免费用于商业与非商业用途，但要求**保留版权声明**、**不得改动角色设计**，且**不得作为发布者的原创角色呈现**。
3. **PySide6 / Qt 不在发布物里。** 它出现在 `requirements.txt`，但 sidecar 构建用 `--exclude-module PySide6` 明确排除，因此 LGPL/GPL 的 Qt 绑定**不随包分发**。本文件的 Python 段按构建实际排除项过滤，而不是按环境里装了什么。
4. **PyInstaller** 是 GPL-2.0-or-later，**但带 bootloader exception**——用它冻结出来的应用可以按任意许可证分发，包括闭源。这条例外是 Joi 能以非开源形式发布的前提之一。

## 许可证全文

本文件只列出组件、版本与许可证标识。各许可证全文随各依赖分发，位于安装后的包目录中；发布物按各许可证要求随包附带。

### npm — Joi Shell（生产依赖，随发布物分发）

共 48 项。

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| @dimforge/rapier3d-compat | 0.12.0 | Apache-2.0 |
| @floating-ui/core | 1.8.0 | MIT |
| @floating-ui/dom | 1.8.0 | MIT |
| @floating-ui/utils | 0.2.12 | MIT |
| @floating-ui/vue | 1.1.11 | MIT |
| @internationalized/date | 3.12.3 | Apache-2.0 |
| @internationalized/number | 3.6.7 | Apache-2.0 |
| @moeru/three-mmd | 0.1.1 | MIT |
| @pixiv/three-vrm | 3.5.5 | MIT |
| @pixiv/three-vrm-animation | 3.5.5 | MIT |
| @pixiv/three-vrm-core | 3.5.5 | MIT |
| @pixiv/three-vrm-materials-hdr-emissive-multiplier | 3.5.5 | MIT |
| @pixiv/three-vrm-materials-mtoon | 3.5.5 | MIT |
| @pixiv/three-vrm-materials-v0compat | 3.5.5 | MIT |
| @pixiv/three-vrm-node-constraint | 3.5.5 | MIT |
| @pixiv/three-vrm-springbone | 3.5.5 | MIT |
| @pixiv/types-vrm-0.0 | 3.5.5 | MIT |
| @pixiv/types-vrmc-materials-hdr-emissive-multiplier-1.0 | 3.5.5 | MIT |
| @pixiv/types-vrmc-materials-mtoon-1.0 | 3.5.5 | MIT |
| @pixiv/types-vrmc-node-constraint-1.0 | 3.5.5 | MIT |
| @pixiv/types-vrmc-springbone-1.0 | 3.5.5 | MIT |
| @pixiv/types-vrmc-springbone-extended-collider-1.0 | 3.5.5 | MIT |
| @pixiv/types-vrmc-vrm-1.0 | 3.5.5 | MIT |
| @pixiv/types-vrmc-vrm-animation-1.0 | 3.5.5 | MIT |
| @swc/helpers | 0.5.23 | Apache-2.0 |
| @tanstack/virtual-core | 3.17.7 | MIT |
| @tanstack/vue-virtual | 3.13.35 | MIT |
| @tauri-apps/api | 2.11.0 | Apache-2.0 OR MIT |
| @tweenjs/tween.js | 23.1.3 | MIT |
| @types/stats.js | 0.17.4 | MIT |
| @types/three | 0.185.1 | MIT |
| @types/web-bluetooth | 0.0.21 | MIT |
| @types/webxr | 0.5.24 | MIT |
| @vue/composition-api |  | UNKNOWN |
| @vueuse/core | 14.4.0 | MIT |
| @vueuse/metadata | 14.4.0 | MIT |
| @vueuse/shared | 14.4.0 | MIT |
| aria-hidden | 1.2.6 | MIT |
| defu | 6.1.7 | MIT |
| fflate | 0.8.3 | MIT |
| lucide-vue-next | 0.577.0 | ISC |
| meshoptimizer | 1.1.1 | MIT |
| ohash | 2.0.11 | MIT |
| reka-ui | 2.10.1 | MIT |
| three | 0.185.1 | MIT |
| tslib | 2.8.1 | 0BSD |
| vue | 3.5.34 | MIT |
| vue-demi | 0.14.10 | UNKNOWN |

### npm — Minecraft bridge（生产依赖，随发布物分发）

共 84 项。

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| @azure/msal-common | 14.16.1 | MIT |
| @azure/msal-node | 2.16.3 | MIT |
| @types/node | 26.2.0 | MIT |
| @types/node-rsa | 1.1.4 | MIT |
| @types/readable-stream | 4.0.24 | MIT |
| @xboxreplay/xboxlive-auth | 5.1.0 | Apache-2.0 |
| abort-controller | 3.0.0 | MIT |
| aes-js | 3.1.2 | MIT |
| asn1 | 0.2.3 | MIT |
| base64-js | 1.5.1 | MIT |
| buffer | 6.0.3 | MIT |
| buffer-equal | 1.0.1 | MIT |
| buffer-equal-constant-time | 1.0.1 | BSD-3-Clause |
| commander | 2.20.3 | MIT |
| debug | 4.4.3 | MIT |
| discontinuous-range | 1.0.0 | MIT |
| ecdsa-sig-formatter | 1.0.11 | Apache-2.0 |
| encoding |  | UNKNOWN |
| endian-toggle | 0.0.0 | MIT |
| event-target-shim | 5.0.1 | MIT |
| events | 3.3.0 | MIT |
| ieee754 | 1.2.1 | BSD-3-Clause |
| jsonwebtoken | 9.0.3 | MIT |
| jwa | 2.0.1 | MIT |
| jws | 4.0.1 | MIT |
| lodash.includes | 4.3.0 | MIT |
| lodash.isboolean | 3.0.3 | MIT |
| lodash.isinteger | 4.0.4 | MIT |
| lodash.isnumber | 3.0.3 | MIT |
| lodash.isplainobject | 4.0.6 | MIT |
| lodash.isstring | 4.0.1 | MIT |
| lodash.merge | 4.6.2 | MIT |
| lodash.once | 4.1.1 | MIT |
| minecraft-data | 3.113.1 | MIT |
| minecraft-folder-path | 1.2.0 | MIT |
| minecraft-protocol | 1.66.2 | BSD-3-Clause |
| mineflayer | 4.37.1 | MIT |
| mineflayer-pathfinder | 2.4.5 | MIT |
| mojangson | 2.0.4 | MIT |
| moo | 0.5.3 | BSD-3-Clause |
| ms | 2.1.3 | MIT |
| nearley | 2.20.1 | MIT |
| node-fetch | 2.7.0 | MIT |
| node-rsa | 0.4.2 | MIT |
| prismarine-auth | 3.1.1 | MIT |
| prismarine-biome | 1.4.0 | MIT |
| prismarine-block | 1.23.0 | MIT |
| prismarine-chat | 1.13.0 | MIT |
| prismarine-chunk | 1.41.0 | MIT |
| prismarine-entity | 2.6.0 | MIT |
| prismarine-item | 1.18.0 | MIT |
| prismarine-nbt | 2.8.0 | MIT |
| prismarine-physics | 1.11.1 | MIT |
| prismarine-realms | 1.6.0 | MIT |
| prismarine-recipe | 1.5.0 | MIT |
| prismarine-registry | 1.12.0 | MIT |
| prismarine-windows | 2.10.0 | MIT |
| prismarine-world | 3.7.0 | MIT |
| process | 0.11.10 | MIT |
| protodef | 1.19.0 | MIT |
| railroad-diagrams | 1.0.0 | CC0-1.0 |
| randexp | 0.4.6 | MIT |
| readable-stream | 4.7.0 | MIT |
| ret | 0.1.15 | MIT |
| rxjs | 7.8.2 | Apache-2.0 |
| safe-buffer | 5.2.1 | MIT |
| semver | 7.8.5 | ISC |
| smart-buffer | 4.2.0 | MIT |
| string_decoder | 1.3.0 | MIT |
| tr46 | 0.0.3 | MIT |
| tslib | 2.8.1 | 0BSD |
| typed-emitter | 2.1.0 | MIT |
| typed-emitter | 1.4.0 | MIT |
| uint4 | 0.1.2 | MIT |
| undici-types | 8.3.0 | MIT |
| uuid | 8.3.2 | MIT |
| uuid | 10.0.0 | MIT |
| uuid-1345 | 1.0.2 | MIT |
| vec3 | 0.1.10 | BSD |
| vec3 | 0.2.0 | BSD |
| webidl-conversions | 3.0.1 | BSD-2-Clause |
| whatwg-url | 5.0.0 | MIT |
| xxhash-wasm | 0.4.2 | MIT |
| yggdrasil | 1.8.0 | MIT |

### Python — Core sidecar（PyInstaller 冻结进包；已排除：PySide6）

共 41 项。

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| altgraph | 0.17.5 | MIT License |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.12.1 | MIT |
| backports.tarfile | 1.2.0 | MIT License |
| certifi | 2026.4.22 | Mozilla Public License 2.0 (MPL 2.0) |
| comtypes | 1.4.16 | MIT |
| distro | 1.9.0 | Apache Software License |
| exceptiongroup | 1.3.1 | MIT License |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| idna | 3.15 | BSD-3-Clause |
| importlib_metadata | 8.7.1 | Apache-2.0 |
| jaraco.classes | 3.4.0 | MIT License |
| jaraco.context | 6.1.1 | MIT |
| jaraco.functools | 4.4.0 | MIT |
| jiter | 0.14.0 | MIT |
| keyring | 25.7.0 | MIT |
| macholib | 1.16.4 | MIT License |
| more-itertools | 10.8.0 | MIT |
| openai | 2.37.0 | Apache Software License |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pillow | 11.3.0 | MIT-CMU |
| pip | 26.0.1 | MIT |
| pydantic | 2.13.4 | MIT |
| pydantic_core | 2.46.4 | MIT |
| pyinstaller | 6.21.0 | GNU General Public License v2 (GPLv2) |
| pyinstaller-hooks-contrib | 2026.6 | Apache Software License; GNU General Public License v2 (GPLv2) |
| pyobjc-core | 11.1 | MIT |
| pyobjc-framework-Cocoa | 11.1 | MIT |
| pyobjc-framework-Quartz | 11.1 | MIT |
| pytesseract | 0.3.13 | Apache Software License |
| PyYAML | 6.0.3 | MIT License |
| setuptools | 58.0.4 | MIT License |
| sniffio | 1.3.1 | MIT License; Apache Software License |
| tqdm | 4.67.3 | MPL-2.0 AND MIT |
| typing-inspection | 0.4.2 | MIT |
| typing_extensions | 4.15.0 | PSF-2.0 |
| uiautomation | 2.0.29 | Apache 2.0 |
| websockets | 15.0.1 | BSD License |
| zipp | 3.23.1 | MIT |

### Rust — Tauri 壳（编译进可执行文件）

共 440 项。

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| adler2 | 2.0.1 | 0BSD OR MIT OR Apache-2.0 |
| aho-corasick | 1.1.4 | Unlicense OR MIT |
| alloc-no-stdlib | 2.0.4 | BSD-3-Clause |
| alloc-stdlib | 0.2.2 | BSD-3-Clause |
| android_system_properties | 0.1.5 | MIT/Apache-2.0 |
| anyhow | 1.0.102 | MIT OR Apache-2.0 |
| atk | 0.18.2 | MIT |
| atk-sys | 0.18.2 | MIT |
| atomic-waker | 1.1.2 | Apache-2.0 OR MIT |
| autocfg | 1.5.0 | Apache-2.0 OR MIT |
| base64 | 0.21.7 | MIT OR Apache-2.0 |
| base64 | 0.22.1 | MIT OR Apache-2.0 |
| bit-set | 0.8.0 | Apache-2.0 OR MIT |
| bit-vec | 0.8.0 | Apache-2.0 OR MIT |
| bitflags | 1.3.2 | MIT/Apache-2.0 |
| bitflags | 2.11.1 | MIT OR Apache-2.0 |
| block-buffer | 0.10.4 | MIT OR Apache-2.0 |
| block2 | 0.6.2 | MIT |
| brotli | 8.0.2 | BSD-3-Clause AND MIT |
| brotli-decompressor | 5.0.0 | BSD-3-Clause/MIT |
| bs58 | 0.5.1 | MIT/Apache-2.0 |
| bumpalo | 3.20.2 | MIT OR Apache-2.0 |
| bytemuck | 1.25.0 | Zlib OR Apache-2.0 OR MIT |
| byteorder | 1.5.0 | Unlicense OR MIT |
| bytes | 1.11.1 | MIT |
| cairo-rs | 0.18.5 | MIT |
| cairo-sys-rs | 0.18.2 | MIT |
| camino | 1.2.2 | MIT OR Apache-2.0 |
| cargo-platform | 0.1.9 | MIT OR Apache-2.0 |
| cargo_metadata | 0.19.2 | MIT |
| cargo_toml | 0.22.3 | Apache-2.0 OR MIT |
| cc | 1.2.62 | MIT OR Apache-2.0 |
| cesu8 | 1.1.0 | Apache-2.0/MIT |
| cfb | 0.7.3 | MIT |
| cfg-expr | 0.15.8 | MIT OR Apache-2.0 |
| cfg-if | 1.0.4 | MIT OR Apache-2.0 |
| chrono | 0.4.44 | MIT OR Apache-2.0 |
| combine | 4.6.7 | MIT |
| cookie | 0.18.1 | MIT OR Apache-2.0 |
| core-foundation | 0.10.1 | MIT OR Apache-2.0 |
| core-foundation-sys | 0.8.7 | MIT OR Apache-2.0 |
| core-graphics | 0.25.0 | MIT OR Apache-2.0 |
| core-graphics-types | 0.2.0 | MIT OR Apache-2.0 |
| cpufeatures | 0.2.17 | MIT OR Apache-2.0 |
| crc32fast | 1.5.0 | MIT OR Apache-2.0 |
| crossbeam-channel | 0.5.15 | MIT OR Apache-2.0 |
| crossbeam-utils | 0.8.21 | MIT OR Apache-2.0 |
| crypto-common | 0.1.7 | MIT OR Apache-2.0 |
| cssparser | 0.36.0 | MPL-2.0 |
| cssparser-macros | 0.6.1 | MPL-2.0 |
| ctor | 0.8.0 | Apache-2.0 OR MIT |
| ctor-proc-macro | 0.0.7 | Apache-2.0 OR MIT |
| darling | 0.23.0 | MIT |
| darling_core | 0.23.0 | MIT |
| darling_macro | 0.23.0 | MIT |
| dbus | 0.9.11 | Apache-2.0/MIT |
| deranged | 0.5.8 | MIT OR Apache-2.0 |
| derive_more | 2.1.1 | MIT |
| derive_more-impl | 2.1.1 | MIT |
| digest | 0.10.7 | MIT OR Apache-2.0 |
| dirs | 6.0.0 | MIT OR Apache-2.0 |
| dirs-sys | 0.5.0 | MIT OR Apache-2.0 |
| dispatch2 | 0.3.1 | Zlib OR Apache-2.0 OR MIT |
| displaydoc | 0.2.5 | MIT OR Apache-2.0 |
| dlopen2 | 0.8.2 | MIT |
| dlopen2_derive | 0.4.3 | MIT |
| dom_query | 0.27.0 | MIT |
| dpi | 0.1.2 | Apache-2.0 AND MIT |
| dtoa | 1.0.11 | MIT OR Apache-2.0 |
| dtoa-short | 0.3.5 | MPL-2.0 |
| dtor | 0.3.0 | Apache-2.0 OR MIT |
| dtor-proc-macro | 0.0.6 | Apache-2.0 OR MIT |
| dunce | 1.0.5 | CC0-1.0 OR MIT-0 OR Apache-2.0 |
| dyn-clone | 1.0.20 | MIT OR Apache-2.0 |
| embed-resource | 3.0.9 | MIT |
| embed_plist | 1.2.2 | MIT OR Apache-2.0 |
| equivalent | 1.0.2 | Apache-2.0 OR MIT |
| erased-serde | 0.4.10 | MIT OR Apache-2.0 |
| fastrand | 2.4.1 | Apache-2.0 OR MIT |
| fdeflate | 0.3.7 | MIT OR Apache-2.0 |
| field-offset | 0.3.6 | MIT OR Apache-2.0 |
| find-msvc-tools | 0.1.9 | MIT OR Apache-2.0 |
| flate2 | 1.1.9 | MIT OR Apache-2.0 |
| fnv | 1.0.7 | Apache-2.0 / MIT |
| foldhash | 0.1.5 | Zlib |
| foldhash | 0.2.0 | Zlib |
| foreign-types | 0.5.0 | MIT/Apache-2.0 |
| foreign-types-macros | 0.2.3 | MIT/Apache-2.0 |
| foreign-types-shared | 0.3.1 | MIT/Apache-2.0 |
| form_urlencoded | 1.2.2 | MIT OR Apache-2.0 |
| futures-channel | 0.3.32 | MIT OR Apache-2.0 |
| futures-core | 0.3.32 | MIT OR Apache-2.0 |
| futures-executor | 0.3.32 | MIT OR Apache-2.0 |
| futures-io | 0.3.32 | MIT OR Apache-2.0 |
| futures-macro | 0.3.32 | MIT OR Apache-2.0 |
| futures-sink | 0.3.32 | MIT OR Apache-2.0 |
| futures-task | 0.3.32 | MIT OR Apache-2.0 |
| futures-util | 0.3.32 | MIT OR Apache-2.0 |
| gdk | 0.18.2 | MIT |
| gdk-pixbuf | 0.18.5 | MIT |
| gdk-pixbuf-sys | 0.18.0 | MIT |
| gdk-sys | 0.18.2 | MIT |
| gdkwayland-sys | 0.18.2 | MIT |
| gdkx11 | 0.18.2 | MIT |
| gdkx11-sys | 0.18.2 | MIT |
| generic-array | 0.14.7 | MIT |
| getrandom | 0.2.17 | MIT OR Apache-2.0 |
| getrandom | 0.3.4 | MIT OR Apache-2.0 |
| getrandom | 0.4.2 | MIT OR Apache-2.0 |
| gio | 0.18.4 | MIT |
| gio-sys | 0.18.1 | MIT |
| glib | 0.18.5 | MIT |
| glib-macros | 0.18.5 | MIT |
| glib-sys | 0.18.1 | MIT |
| glob | 0.3.3 | MIT OR Apache-2.0 |
| gobject-sys | 0.18.0 | MIT |
| gtk | 0.18.2 | MIT |
| gtk-sys | 0.18.2 | MIT |
| gtk3-macros | 0.18.2 | MIT |
| hashbrown | 0.12.3 | MIT OR Apache-2.0 |
| hashbrown | 0.15.5 | MIT OR Apache-2.0 |
| hashbrown | 0.17.1 | MIT OR Apache-2.0 |
| heck | 0.4.1 | MIT OR Apache-2.0 |
| heck | 0.5.0 | MIT OR Apache-2.0 |
| hex | 0.4.3 | MIT OR Apache-2.0 |
| html5ever | 0.38.0 | MIT OR Apache-2.0 |
| http | 1.4.0 | MIT OR Apache-2.0 |
| http-body | 1.0.1 | MIT |
| http-body-util | 0.1.3 | MIT |
| httparse | 1.10.1 | MIT OR Apache-2.0 |
| hyper | 1.9.0 | MIT |
| hyper-util | 0.1.20 | MIT |
| iana-time-zone | 0.1.65 | MIT OR Apache-2.0 |
| iana-time-zone-haiku | 0.1.2 | MIT OR Apache-2.0 |
| ico | 0.5.0 | MIT |
| icu_collections | 2.2.0 | Unicode-3.0 |
| icu_locale_core | 2.2.0 | Unicode-3.0 |
| icu_normalizer | 2.2.0 | Unicode-3.0 |
| icu_normalizer_data | 2.2.0 | Unicode-3.0 |
| icu_properties | 2.2.0 | Unicode-3.0 |
| icu_properties_data | 2.2.0 | Unicode-3.0 |
| icu_provider | 2.2.0 | Unicode-3.0 |
| id-arena | 2.3.0 | MIT/Apache-2.0 |
| ident_case | 1.0.1 | MIT/Apache-2.0 |
| idna | 1.1.0 | MIT OR Apache-2.0 |
| idna_adapter | 1.2.2 | Apache-2.0 OR MIT |
| indexmap | 1.9.3 | Apache-2.0 OR MIT |
| indexmap | 2.14.0 | Apache-2.0 OR MIT |
| infer | 0.19.0 | MIT |
| ipnet | 2.12.0 | MIT OR Apache-2.0 |
| itoa | 1.0.18 | MIT OR Apache-2.0 |
| javascriptcore-rs | 1.1.2 | MIT |
| javascriptcore-rs-sys | 1.1.1 | MIT |
| jni | 0.21.1 | MIT/Apache-2.0 |
| jni-sys | 0.3.1 | MIT OR Apache-2.0 |
| jni-sys | 0.4.1 | MIT OR Apache-2.0 |
| jni-sys-macros | 0.4.1 | MIT OR Apache-2.0 |
| js-sys | 0.3.98 | MIT OR Apache-2.0 |
| json-patch | 3.0.1 | MIT/Apache-2.0 |
| jsonptr | 0.6.3 | MIT OR Apache-2.0 |
| keyboard-types | 0.7.0 | MIT OR Apache-2.0 |
| leb128fmt | 0.1.0 | MIT OR Apache-2.0 |
| libappindicator | 0.9.0 | Apache-2.0 OR MIT |
| libappindicator-sys | 0.9.0 | Apache-2.0 OR MIT |
| libc | 0.2.186 | MIT OR Apache-2.0 |
| libdbus-sys | 0.2.7 | Apache-2.0/MIT |
| libloading | 0.7.4 | ISC |
| libredox | 0.1.16 | MIT |
| litemap | 0.8.2 | Unicode-3.0 |
| lock_api | 0.4.14 | MIT OR Apache-2.0 |
| log | 0.4.29 | MIT OR Apache-2.0 |
| markup5ever | 0.38.0 | MIT OR Apache-2.0 |
| memchr | 2.8.0 | Unlicense OR MIT |
| memoffset | 0.9.1 | MIT |
| mime | 0.3.17 | MIT OR Apache-2.0 |
| miniz_oxide | 0.8.9 | MIT OR Zlib OR Apache-2.0 |
| mio | 1.2.0 | MIT |
| muda | 0.19.1 | Apache-2.0 OR MIT |
| ndk | 0.9.0 | MIT OR Apache-2.0 |
| ndk-sys | 0.6.0+11769913 | MIT OR Apache-2.0 |
| new_debug_unreachable | 1.0.6 | MIT |
| num-conv | 0.2.1 | MIT OR Apache-2.0 |
| num-traits | 0.2.19 | MIT OR Apache-2.0 |
| num_enum | 0.7.6 | BSD-3-Clause OR MIT OR Apache-2.0 |
| num_enum_derive | 0.7.6 | BSD-3-Clause OR MIT OR Apache-2.0 |
| objc2 | 0.6.4 | MIT |
| objc2-app-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-cloud-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-core-data | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-core-foundation | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-core-graphics | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-core-image | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-core-location | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-core-text | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-encode | 4.1.0 | MIT |
| objc2-exception-helper | 0.1.1 | Zlib OR Apache-2.0 OR MIT |
| objc2-foundation | 0.3.2 | MIT |
| objc2-io-surface | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-quartz-core | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-ui-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-user-notifications | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| objc2-web-kit | 0.3.2 | Zlib OR Apache-2.0 OR MIT |
| once_cell | 1.21.4 | MIT OR Apache-2.0 |
| option-ext | 0.2.0 | MPL-2.0 |
| pango | 0.18.3 | MIT |
| pango-sys | 0.18.0 | MIT |
| parking_lot | 0.12.5 | MIT OR Apache-2.0 |
| parking_lot_core | 0.9.12 | MIT OR Apache-2.0 |
| percent-encoding | 2.3.2 | MIT OR Apache-2.0 |
| phf | 0.13.1 | MIT |
| phf_codegen | 0.13.1 | MIT |
| phf_generator | 0.13.1 | MIT |
| phf_macros | 0.13.1 | MIT |
| phf_shared | 0.13.1 | MIT |
| pin-project-lite | 0.2.17 | Apache-2.0 OR MIT |
| pkg-config | 0.3.33 | MIT OR Apache-2.0 |
| plist | 1.9.0 | MIT |
| png | 0.17.16 | MIT OR Apache-2.0 |
| png | 0.18.1 | MIT OR Apache-2.0 |
| potential_utf | 0.1.5 | Unicode-3.0 |
| powerfmt | 0.2.0 | MIT OR Apache-2.0 |
| ppv-lite86 | 0.2.21 | MIT OR Apache-2.0 |
| precomputed-hash | 0.1.1 | MIT |
| prettyplease | 0.2.37 | MIT OR Apache-2.0 |
| proc-macro-crate | 1.3.1 | MIT OR Apache-2.0 |
| proc-macro-crate | 2.0.2 | MIT OR Apache-2.0 |
| proc-macro-crate | 3.5.0 | MIT OR Apache-2.0 |
| proc-macro-error | 1.0.4 | MIT OR Apache-2.0 |
| proc-macro-error-attr | 1.0.4 | MIT OR Apache-2.0 |
| proc-macro2 | 1.0.106 | MIT OR Apache-2.0 |
| quick-xml | 0.39.4 | MIT |
| quote | 1.0.45 | MIT OR Apache-2.0 |
| r-efi | 5.3.0 | MIT OR Apache-2.0 OR LGPL-2.1-or-later |
| r-efi | 6.0.0 | MIT OR Apache-2.0 OR LGPL-2.1-or-later |
| rand | 0.9.5 | MIT OR Apache-2.0 |
| rand_chacha | 0.9.0 | MIT OR Apache-2.0 |
| rand_core | 0.9.5 | MIT OR Apache-2.0 |
| raw-window-handle | 0.6.2 | MIT OR Apache-2.0 OR Zlib |
| redox_syscall | 0.5.18 | MIT |
| redox_users | 0.5.2 | MIT |
| ref-cast | 1.0.25 | MIT OR Apache-2.0 |
| ref-cast-impl | 1.0.25 | MIT OR Apache-2.0 |
| regex | 1.12.3 | MIT OR Apache-2.0 |
| regex-automata | 0.4.14 | MIT OR Apache-2.0 |
| regex-syntax | 0.8.10 | MIT OR Apache-2.0 |
| reqwest | 0.13.3 | MIT OR Apache-2.0 |
| rustc-hash | 2.1.2 | Apache-2.0 OR MIT |
| rustc_version | 0.4.1 | MIT OR Apache-2.0 |
| rustversion | 1.0.22 | MIT OR Apache-2.0 |
| same-file | 1.0.6 | Unlicense/MIT |
| schemars | 0.8.22 | MIT |
| schemars | 0.9.0 | MIT |
| schemars | 1.2.1 | MIT |
| schemars_derive | 0.8.22 | MIT |
| scopeguard | 1.2.0 | MIT OR Apache-2.0 |
| selectors | 0.36.1 | MPL-2.0 |
| semver | 1.0.28 | MIT OR Apache-2.0 |
| serde | 1.0.228 | MIT OR Apache-2.0 |
| serde-untagged | 0.1.9 | MIT OR Apache-2.0 |
| serde_core | 1.0.228 | MIT OR Apache-2.0 |
| serde_derive | 1.0.228 | MIT OR Apache-2.0 |
| serde_derive_internals | 0.29.1 | MIT OR Apache-2.0 |
| serde_json | 1.0.149 | MIT OR Apache-2.0 |
| serde_repr | 0.1.20 | MIT OR Apache-2.0 |
| serde_spanned | 0.6.9 | MIT OR Apache-2.0 |
| serde_spanned | 1.1.1 | MIT OR Apache-2.0 |
| serde_with | 3.20.0 | MIT OR Apache-2.0 |
| serde_with_macros | 3.20.0 | MIT OR Apache-2.0 |
| serialize-to-javascript | 0.1.2 | MIT OR Apache-2.0 |
| serialize-to-javascript-impl | 0.1.2 | MIT OR Apache-2.0 |
| servo_arc | 0.4.3 | MIT OR Apache-2.0 |
| sha2 | 0.10.9 | MIT OR Apache-2.0 |
| shlex | 1.3.0 | MIT OR Apache-2.0 |
| simd-adler32 | 0.3.9 | MIT |
| siphasher | 1.0.3 | MIT/Apache-2.0 |
| slab | 0.4.12 | MIT |
| smallvec | 1.15.1 | MIT OR Apache-2.0 |
| socket2 | 0.6.3 | MIT OR Apache-2.0 |
| softbuffer | 0.4.8 | MIT OR Apache-2.0 |
| soup3 | 0.5.0 | MIT |
| soup3-sys | 0.5.0 | MIT |
| stable_deref_trait | 1.2.1 | MIT OR Apache-2.0 |
| string_cache | 0.9.0 | MIT OR Apache-2.0 |
| string_cache_codegen | 0.6.1 | MIT OR Apache-2.0 |
| strsim | 0.11.1 | MIT |
| swift-rs | 1.0.7 | MIT OR Apache-2.0 |
| syn | 1.0.109 | MIT OR Apache-2.0 |
| syn | 2.0.117 | MIT OR Apache-2.0 |
| sync_wrapper | 1.0.2 | Apache-2.0 |
| synstructure | 0.13.2 | MIT |
| system-deps | 6.2.2 | MIT OR Apache-2.0 |
| tao | 0.35.2 | Apache-2.0 |
| tao-macros | 0.1.3 | MIT OR Apache-2.0 |
| target-lexicon | 0.12.16 | Apache-2.0 WITH LLVM-exception |
| tauri | 2.11.1 | Apache-2.0 OR MIT |
| tauri-build | 2.6.1 | Apache-2.0 OR MIT |
| tauri-codegen | 2.6.1 | Apache-2.0 OR MIT |
| tauri-macros | 2.6.1 | Apache-2.0 OR MIT |
| tauri-runtime | 2.11.1 | Apache-2.0 OR MIT |
| tauri-runtime-wry | 2.11.1 | Apache-2.0 OR MIT |
| tauri-utils | 2.9.1 | Apache-2.0 OR MIT |
| tauri-winres | 0.3.6 | MIT |
| tendril | 0.5.0 | MIT OR Apache-2.0 |
| thiserror | 1.0.69 | MIT OR Apache-2.0 |
| thiserror | 2.0.18 | MIT OR Apache-2.0 |
| thiserror-impl | 1.0.69 | MIT OR Apache-2.0 |
| thiserror-impl | 2.0.18 | MIT OR Apache-2.0 |
| time | 0.3.47 | MIT OR Apache-2.0 |
| time-core | 0.1.8 | MIT OR Apache-2.0 |
| time-macros | 0.2.27 | MIT OR Apache-2.0 |
| tinystr | 0.8.3 | Unicode-3.0 |
| tinyvec | 1.11.0 | Zlib OR Apache-2.0 OR MIT |
| tinyvec_macros | 0.1.1 | MIT OR Apache-2.0 OR Zlib |
| tokio | 1.52.3 | MIT |
| tokio-util | 0.7.18 | MIT |
| toml | 0.8.2 | MIT OR Apache-2.0 |
| toml | 0.9.12+spec-1.1.0 | MIT OR Apache-2.0 |
| toml | 1.1.2+spec-1.1.0 | MIT OR Apache-2.0 |
| toml_datetime | 0.6.3 | MIT OR Apache-2.0 |
| toml_datetime | 0.7.5+spec-1.1.0 | MIT OR Apache-2.0 |
| toml_datetime | 1.1.1+spec-1.1.0 | MIT OR Apache-2.0 |
| toml_edit | 0.19.15 | MIT OR Apache-2.0 |
| toml_edit | 0.20.2 | MIT OR Apache-2.0 |
| toml_edit | 0.25.11+spec-1.1.0 | MIT OR Apache-2.0 |
| toml_parser | 1.1.2+spec-1.1.0 | MIT OR Apache-2.0 |
| toml_writer | 1.1.1+spec-1.1.0 | MIT OR Apache-2.0 |
| tower | 0.5.3 | MIT |
| tower-http | 0.6.10 | MIT |
| tower-layer | 0.3.3 | MIT |
| tower-service | 0.3.3 | MIT |
| tracing | 0.1.44 | MIT |
| tracing-core | 0.1.36 | MIT |
| tray-icon | 0.23.1 | MIT OR Apache-2.0 |
| try-lock | 0.2.5 | MIT |
| typeid | 1.0.3 | MIT OR Apache-2.0 |
| typenum | 1.20.0 | MIT OR Apache-2.0 |
| unic-char-property | 0.9.0 | MIT/Apache-2.0 |
| unic-char-range | 0.9.0 | MIT/Apache-2.0 |
| unic-common | 0.9.0 | MIT/Apache-2.0 |
| unic-ucd-ident | 0.9.0 | MIT/Apache-2.0 |
| unic-ucd-version | 0.9.0 | MIT/Apache-2.0 |
| unicode-ident | 1.0.24 | (MIT OR Apache-2.0) AND Unicode-3.0 |
| unicode-segmentation | 1.13.2 | MIT OR Apache-2.0 |
| unicode-xid | 0.2.6 | MIT OR Apache-2.0 |
| url | 2.5.8 | MIT OR Apache-2.0 |
| urlpattern | 0.3.0 | MIT |
| utf-8 | 0.7.6 | MIT OR Apache-2.0 |
| utf8_iter | 1.0.4 | Apache-2.0 OR MIT |
| uuid | 1.23.1 | Apache-2.0 OR MIT |
| version-compare | 0.2.1 | MIT |
| version_check | 0.9.5 | MIT/Apache-2.0 |
| vswhom | 0.1.0 | MIT |
| vswhom-sys | 0.1.3 | MIT |
| walkdir | 2.5.0 | Unlicense/MIT |
| want | 0.3.1 | MIT |
| wasi | 0.11.1+wasi-snapshot-preview1 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wasip2 | 1.0.3+wasi-0.2.9 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wasip3 | 0.4.0+wasi-0.3.0-rc-2026-01-06 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wasm-bindgen | 0.2.121 | MIT OR Apache-2.0 |
| wasm-bindgen-futures | 0.4.71 | MIT OR Apache-2.0 |
| wasm-bindgen-macro | 0.2.121 | MIT OR Apache-2.0 |
| wasm-bindgen-macro-support | 0.2.121 | MIT OR Apache-2.0 |
| wasm-bindgen-shared | 0.2.121 | MIT OR Apache-2.0 |
| wasm-encoder | 0.244.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wasm-metadata | 0.244.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wasm-streams | 0.5.0 | MIT OR Apache-2.0 |
| wasmparser | 0.244.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| web-sys | 0.3.98 | MIT OR Apache-2.0 |
| web_atoms | 0.2.4 | MIT OR Apache-2.0 |
| webkit2gtk | 2.0.2 | MIT |
| webkit2gtk-sys | 2.0.2 | MIT |
| webview2-com | 0.38.2 | MIT |
| webview2-com-macros | 0.8.1 | MIT |
| webview2-com-sys | 0.38.2 | MIT |
| winapi | 0.3.9 | MIT/Apache-2.0 |
| winapi-i686-pc-windows-gnu | 0.4.0 | MIT/Apache-2.0 |
| winapi-util | 0.1.11 | Unlicense OR MIT |
| winapi-x86_64-pc-windows-gnu | 0.4.0 | MIT/Apache-2.0 |
| window-vibrancy | 0.6.0 | Apache-2.0 OR MIT |
| windows | 0.61.3 | MIT OR Apache-2.0 |
| windows-collections | 0.2.0 | MIT OR Apache-2.0 |
| windows-core | 0.61.2 | MIT OR Apache-2.0 |
| windows-core | 0.62.2 | MIT OR Apache-2.0 |
| windows-future | 0.2.1 | MIT OR Apache-2.0 |
| windows-implement | 0.60.2 | MIT OR Apache-2.0 |
| windows-interface | 0.59.3 | MIT OR Apache-2.0 |
| windows-link | 0.1.3 | MIT OR Apache-2.0 |
| windows-link | 0.2.1 | MIT OR Apache-2.0 |
| windows-numerics | 0.2.0 | MIT OR Apache-2.0 |
| windows-result | 0.3.4 | MIT OR Apache-2.0 |
| windows-result | 0.4.1 | MIT OR Apache-2.0 |
| windows-strings | 0.4.2 | MIT OR Apache-2.0 |
| windows-strings | 0.5.1 | MIT OR Apache-2.0 |
| windows-sys | 0.45.0 | MIT OR Apache-2.0 |
| windows-sys | 0.59.0 | MIT OR Apache-2.0 |
| windows-sys | 0.61.2 | MIT OR Apache-2.0 |
| windows-targets | 0.42.2 | MIT OR Apache-2.0 |
| windows-targets | 0.52.6 | MIT OR Apache-2.0 |
| windows-threading | 0.1.0 | MIT OR Apache-2.0 |
| windows-version | 0.1.7 | MIT OR Apache-2.0 |
| windows_aarch64_gnullvm | 0.42.2 | MIT OR Apache-2.0 |
| windows_aarch64_gnullvm | 0.52.6 | MIT OR Apache-2.0 |
| windows_aarch64_msvc | 0.42.2 | MIT OR Apache-2.0 |
| windows_aarch64_msvc | 0.52.6 | MIT OR Apache-2.0 |
| windows_i686_gnu | 0.42.2 | MIT OR Apache-2.0 |
| windows_i686_gnu | 0.52.6 | MIT OR Apache-2.0 |
| windows_i686_gnullvm | 0.52.6 | MIT OR Apache-2.0 |
| windows_i686_msvc | 0.42.2 | MIT OR Apache-2.0 |
| windows_i686_msvc | 0.52.6 | MIT OR Apache-2.0 |
| windows_x86_64_gnu | 0.42.2 | MIT OR Apache-2.0 |
| windows_x86_64_gnu | 0.52.6 | MIT OR Apache-2.0 |
| windows_x86_64_gnullvm | 0.42.2 | MIT OR Apache-2.0 |
| windows_x86_64_gnullvm | 0.52.6 | MIT OR Apache-2.0 |
| windows_x86_64_msvc | 0.42.2 | MIT OR Apache-2.0 |
| windows_x86_64_msvc | 0.52.6 | MIT OR Apache-2.0 |
| winnow | 0.5.40 | MIT |
| winnow | 0.7.15 | MIT |
| winnow | 1.0.2 | MIT |
| winreg | 0.55.0 | MIT |
| wit-bindgen | 0.51.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wit-bindgen | 0.57.1 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wit-bindgen-core | 0.51.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wit-bindgen-rust | 0.51.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wit-bindgen-rust-macro | 0.51.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wit-component | 0.244.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| wit-parser | 0.244.0 | Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT |
| writeable | 0.6.3 | Unicode-3.0 |
| wry | 0.55.1 | Apache-2.0 OR MIT |
| x11 | 2.21.0 | MIT |
| x11-dl | 2.21.0 | MIT |
| yoke | 0.8.2 | Unicode-3.0 |
| yoke-derive | 0.8.2 | Unicode-3.0 |
| zerocopy | 0.8.54 | BSD-2-Clause OR Apache-2.0 OR MIT |
| zerocopy-derive | 0.8.54 | BSD-2-Clause OR Apache-2.0 OR MIT |
| zerofrom | 0.1.8 | Unicode-3.0 |
| zerofrom-derive | 0.1.7 | Unicode-3.0 |
| zerotrie | 0.2.4 | Unicode-3.0 |
| zerovec | 0.11.6 | Unicode-3.0 |
| zerovec-derive | 0.11.3 | Unicode-3.0 |
| zmij | 1.0.21 | MIT |

### 资产与内联代码（无 lockfile，需单独确认权利）

共 6 项。

| 组件 | 版本 | 许可证 |
| --- | --- | --- |
| Node.js runtime | 22.x (staged from the build machine) | MIT, plus the runtime's own bundled third-party notices<br>Copied into the Minecraft bridge bundle so a release can run the adapter without a system Node. Its own notice file ships with the runtime. |
| minecraft-data | 3.113.1 | MIT<br>Vendored into the bridge bundle rather than resolved at runtime; the staging step refuses any version other than the reviewed one. |
| thinking-orbs | 0.2.0 | MIT<br>Vendored, not installed: published as a React component while this Shell is Vue, so agent_companion/shell/src/vendor/thinkingOrbs.js carries its canvas painters with the React wrapper removed and nothing else changed. Copyright (c) 2026 Jakub Antalik, https://orbs.jakubantalik.com — the copyright notice is retained in the file. |
| Live2D Cubism Core for Web | as shipped in the licensed asset archive | Proprietary — Live2D Inc.<br>Not open source and not covered by this repository's LICENSE. Redistribution is governed by Live2D's own SDK licence, and an application that loads user-supplied Live2D models is an 'Expandable Application' under those terms. See docs/RELEASE_HANDS_ON.md. |
| Live2D Cubism sample model — Hiyori Momose (桃瀬ひより) | sample data | Live2D Cubism Sample Data Terms of Use / Free Material License Agreement — © Live2D Inc.<br>The default character's Live2D artwork is Live2D's own sample model, owned and copyrighted by Live2D Inc. Its terms permit free commercial and non-commercial use by General Users and Small-Scale Enterprises, but require the copyright notice, forbid changes to the character's design, and do not allow it to be presented as the publisher's original character. |
| pixi.js (as bundled in the Live2D runtime archive) | as shipped in the licensed asset archive | MIT<br>Distributed inside the same licensed asset archive as the Cubism runtime. |
