# Third-Party Notices (MVP Draft)

This inventory records the primary directly bundled runtimes as of Joi 0.1.0. It is not yet a complete transitive-dependency notice and must be regenerated and reviewed for every release artifact.

| Component | Version in current lock/environment | License |
| --- | ---: | --- |
| Tauri / `@tauri-apps/api` | 2.11.x | Apache-2.0 OR MIT |
| Vue | 3.5.34 | MIT |
| Three.js | 0.185.1 | MIT |
| `@pixiv/three-vrm` | 3.5.5 | MIT |
| Lucide Vue Next | 0.577.0 | ISC |
| Reka UI | 2.10.1 | MIT |
| `thinking-orbs` (vendored, see below) | 0.2.0 | MIT |
| PyInstaller | 6.21.0 | GPL-2.0-or-later with the PyInstaller bootloader exception |
| websockets | 15.0.1 | BSD-3-Clause |
| PyYAML | 6.0.3 | MIT |
| OpenAI Python SDK | 2.37.0 | Apache-2.0 |
| keyring | 25.7.0 | MIT |
| rand | 0.9.5 | MIT OR Apache-2.0 |
| Node.js runtime | 22.22.3 | MIT and bundled third-party notices |
| Mineflayer | 4.37.1 | MIT |
| mineflayer-pathfinder | 2.4.5 | MIT |
| minecraft-data | 3.113.1 | MIT |

`thinking-orbs` is vendored rather than installed: it is published as a React
component and this shell is Vue, so `agent_companion/shell/src/vendor/thinkingOrbs.js`
carries its canvas painters with the React wrapper removed and nothing else
changed. MIT, Copyright (c) 2026 Jakub Antalik — https://orbs.jakubantalik.com.
The copy retains the copyright notice; the full license text must ship with the
release artifact like any other MIT dependency.

Live2D Cubism SDK/Core and the Joi Live2D model are not covered by the open-source licenses above. Distribution must follow their separate licenses and any required Live2D **Expandable Application** approval. Character-pack creators remain responsible for declaring the source, author, license, and permissions of imported assets.

Before publishing a GitHub Release:

1. Generate a complete notice from `Cargo.lock`, `package-lock.json`, the PyInstaller analysis, and bundled static scripts.
2. Include the applicable license texts in the distributed package or an adjacent notices archive.
3. Confirm the approved Joi model, voice, fonts, icon, and backgrounds have explicit redistribution rights.
4. Compare the final DMG contents with this inventory and resolve every unknown binary or asset.
