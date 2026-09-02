"""Regenerate docs/THIRD_PARTY_NOTICES.md from the lockfiles that actually ship.

A notice list written by hand goes stale the first time a dependency moves, and
a stale notice is worse than none: it is a claim about what is inside the
artifact. This reads the three ecosystems a Joi release bundles and reports what
they resolve to right now.

    .venv/bin/python tools/generate_third_party_notices.py            # write
    .venv/bin/python tools/generate_third_party_notices.py --check    # CI mode

Coverage and its limits, both stated in the generated file rather than assumed:

- npm: the *production* dependency tree of the Shell and the Minecraft bridge.
  Build-time tooling (Vite, TypeScript, the Tauri CLI, ncc) is not distributed
  and is not listed.
- Python: the distributions PyInstaller freezes into the Core sidecar, read from
  the environment that would build it.
- Rust: the resolved crate graph behind the Tauri shell, via `cargo metadata`.
  Without a warm registry the crate versions still come from Cargo.lock, but the
  licences cannot be resolved and the run refuses rather than guessing.
- Assets and vendored code carry their own terms and are curated by hand below,
  because no lockfile knows about them.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "THIRD_PARTY_NOTICES.md"

NPM_PROJECTS = (
    ("Joi Shell", ROOT / "agent_companion" / "shell"),
    ("Minecraft bridge", ROOT / "agent_companion" / "adapters" / "minecraft-bridge"),
)

# Things no lockfile can describe: binaries staged at build time, vendored
# source, and licensed artwork. Each needs its own confirmed terms.
CURATED: tuple[dict[str, str], ...] = (
    {
        "name": "Node.js runtime",
        "version": "22.x (staged from the build machine)",
        "license": "MIT, plus the runtime's own bundled third-party notices",
        "note": "Copied into the Minecraft bridge bundle so a release can run the adapter without a system Node. Its own notice file ships with the runtime.",
    },
    {
        "name": "minecraft-data",
        "version": "3.113.1",
        "license": "MIT",
        "note": "Vendored into the bridge bundle rather than resolved at runtime; the staging step refuses any version other than the reviewed one.",
    },
    {
        "name": "thinking-orbs",
        "version": "0.2.0",
        "license": "MIT",
        "note": "Vendored, not installed: published as a React component while this Shell is Vue, so agent_companion/shell/src/vendor/thinkingOrbs.js carries its canvas painters with the React wrapper removed and nothing else changed. Copyright (c) 2026 Jakub Antalik, https://orbs.jakubantalik.com — the copyright notice is retained in the file.",
    },
    {
        "name": "Live2D Cubism Core for Web",
        "version": "as shipped in the licensed asset archive",
        "license": "Proprietary — Live2D Inc.",
        "note": "Not open source and not covered by this repository's LICENSE. Redistribution is governed by Live2D's own SDK licence. Models you import yourself are used under their own rights holders' terms; this application grants no rights in them.",
    },
    {
        "name": "Live2D Cubism sample model — Hiyori Momose (桃瀬ひより)",
        "version": "sample data",
        "license": "Live2D Cubism Sample Data Terms of Use / Free Material License Agreement — © Live2D Inc.",
        "note": "The default character's Live2D artwork is Live2D's own sample model, owned and copyrighted by Live2D Inc. Its terms permit free commercial and non-commercial use by General Users and Small-Scale Enterprises, but require the copyright notice, forbid changes to the character's design, and do not allow it to be presented as the publisher's original character.",
    },
    {
        "name": "pixi.js (as bundled in the Live2D runtime archive)",
        "version": "as shipped in the licensed asset archive",
        "license": "MIT",
        "note": "Distributed inside the same licensed asset archive as the Cubism runtime.",
    },
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate the third-party notices from lockfiles.")
    parser.add_argument("--check", action="store_true", help="Fail if the generated file differs from the committed one.")
    args = parser.parse_args(argv)

    sections: list[str] = []
    total = 0
    for label, project in NPM_PROJECTS:
        rows = _npm_packages(project)
        total += len(rows)
        sections.append(_render_table(f"npm — {label}（生产依赖，随发布物分发）", rows))
    python_rows = _python_packages()
    total += len(python_rows)
    excluded = "、".join(excluded_python_modules()) or "无"
    sections.append(
        _render_table(
            f"Python — Core sidecar（PyInstaller 冻结进包；已排除：{excluded}）",
            python_rows,
        )
    )
    cargo_rows = _cargo_packages()
    total += len(cargo_rows)
    sections.append(_render_table("Rust — Tauri 壳（编译进可执行文件）", cargo_rows))
    sections.append(_render_table("资产与内联代码（无 lockfile，需单独确认权利）", CURATED))
    total += len(CURATED)

    document = _render_document(sections, total)
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != document:
            print("third-party notices are out of date; run tools/generate_third_party_notices.py")
            return 1
        print(f"third-party notices up to date ({total} entries)")
        return 0
    OUTPUT.write_text(document, encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)} ({total} entries)")
    return 0


def _npm_packages(project: Path) -> list[dict[str, str]]:
    if not (project / "node_modules").is_dir():
        raise SystemExit(f"install dependencies first: {project.relative_to(ROOT)}")
    result = subprocess.run(
        ["npm", "ls", "--omit=dev", "--all", "--json"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    # `npm ls` exits non-zero on peer-dependency complaints while still printing
    # a usable tree, so the payload is what decides, not the exit code.
    try:
        tree = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"npm ls produced no tree for {project.relative_to(ROOT)}: {result.stderr[:200]}")
    found: dict[tuple[str, str], dict[str, str]] = {}

    def walk(node: dict[str, Any]) -> None:
        for name, info in (node.get("dependencies") or {}).items():
            version = str(info.get("version") or "")
            key = (name, version)
            if key in found:
                continue
            found[key] = {"name": name, "version": version, "license": _npm_license(project, name)}
            walk(info)

    walk(tree)
    return sorted(found.values(), key=lambda row: row["name"].casefold())


def _npm_license(project: Path, name: str) -> str:
    manifest = project / "node_modules" / Path(*name.split("/")) / "package.json"
    if not manifest.is_file():
        return "UNKNOWN"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "UNKNOWN"
    license_value = data.get("license") or data.get("licenses")
    if isinstance(license_value, list):
        return " OR ".join(str(entry.get("type") if isinstance(entry, dict) else entry) for entry in license_value)
    if isinstance(license_value, dict):
        return str(license_value.get("type") or "UNKNOWN")
    return str(license_value or "UNKNOWN")


# Distributions that exist only to serve an excluded module. PyInstaller's
# --exclude-module names a module, not a distribution, so the family has to be
# named here or the notice claims to ship something the build leaves out.
_EXCLUDED_COMPANIONS = {"pyside6": ("shiboken6",)}


def _python_packages() -> list[dict[str, str]]:
    import importlib.metadata as metadata

    excluded = _excluded_python_distributions()
    rows: dict[str, dict[str, str]] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name or name in rows:
            continue
        if _normalized(name) in excluded:
            continue
        rows[name] = {
            "name": name,
            "version": dist.version or "",
            "license": _python_license(dist.metadata),
        }
    return sorted(rows.values(), key=lambda row: row["name"].casefold())


def excluded_python_modules() -> list[str]:
    """The modules the sidecar build refuses to freeze, read from the build itself.

    An installed package is not a shipped package. Reading the exclusions from
    the build script rather than restating them means the notice cannot drift
    into claiming a dependency the artifact does not contain -- which for a
    copyleft dependency is not a cosmetic error.
    """

    source = (ROOT / "tools" / "build_core_sidecar.py").read_text(encoding="utf-8")
    modules: list[str] = []
    parts = source.split('"--exclude-module",')
    for part in parts[1:]:
        head = part.split('"', 2)
        if len(head) >= 2:
            modules.append(head[1])
    return modules


def _excluded_python_distributions() -> set[str]:
    excluded: set[str] = set()
    for module in excluded_python_modules():
        key = _normalized(module)
        excluded.add(key)
        # PySide6 installs itself as several distributions; excluding the module
        # leaves all of them out of the frozen application.
        excluded.update(_normalized(name) for name in _EXCLUDED_COMPANIONS.get(key, ()))
    import importlib.metadata as metadata

    for dist in metadata.distributions():
        name = dist.metadata["Name"] or ""
        normalized = _normalized(name)
        if any(normalized.startswith(f"{key}-") or normalized.startswith(f"{key}_") for key in tuple(excluded)):
            excluded.add(normalized)
    return excluded


def _normalized(name: str) -> str:
    return name.strip().casefold().replace("_", "-")


def _python_license(meta: Any) -> str:
    declared = (meta.get("License-Expression") or "").strip()
    if declared:
        return declared
    classifiers = [value for value in meta.get_all("Classifier") or [] if value.startswith("License ::")]
    if classifiers:
        return "; ".join(value.split("::")[-1].strip() for value in classifiers)
    legacy = (meta.get("License") or "").strip()
    # Some projects paste their whole licence text into this field.
    return legacy.splitlines()[0][:60] if legacy else "UNKNOWN"


def _cargo_packages() -> list[dict[str, str]]:
    manifest = ROOT / "agent_companion" / "shell" / "src-tauri" / "Cargo.toml"
    result = subprocess.run(
        ["cargo", "metadata", "--format-version", "1"],
        cwd=manifest.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            "cargo metadata failed, so crate licences cannot be resolved. Run it once with network access "
            f"to warm the registry index, then regenerate.\n{result.stderr[-400:]}"
        )
    data = json.loads(result.stdout)
    rows = [
        {
            "name": package["name"],
            "version": package.get("version", ""),
            "license": package.get("license") or ("see " + package["license_file"] if package.get("license_file") else "UNKNOWN"),
        }
        for package in data.get("packages", [])
        if package.get("name") != "joi-shell"
    ]
    return sorted(rows, key=lambda row: row["name"].casefold())


def _render_table(title: str, rows: Iterable[dict[str, str]]) -> str:
    rows = list(rows)
    lines = [f"### {title}", "", f"共 {len(rows)} 项。", "", "| 组件 | 版本 | 许可证 |", "| --- | --- | --- |"]
    for row in rows:
        note = row.get("note")
        license_cell = row["license"].replace("|", "/")
        if note:
            license_cell = f"{license_cell}<br>{note}"
        lines.append(f"| {row['name']} | {row['version']} | {license_cell} |")
    lines.append("")
    return "\n".join(lines)


def _render_document(sections: list[str], total: int) -> str:
    header = f"""# 第三方通知 / Third-Party Notices

> **本文件由 `tools/generate_third_party_notices.py` 生成，不要手工编辑。**
> 依赖一变它就过期，而过期的通知比没有更糟——它是一份关于「包里到底装了什么」的声明。
> 每次发布前重新生成：`.venv/bin/python tools/generate_third_party_notices.py`

Joi 自身的代码按仓库根目录的 `LICENSE` 授权（保留所有权利，非开源）。
**本文件列出的组件不受该 LICENSE 约束**，它们各自由其作者按下列许可证授权。

当前共 {total} 项。

## 覆盖范围与边界

- **npm**：Shell 与 Minecraft 桥接的**生产依赖树**。构建工具（Vite、TypeScript、Tauri CLI、ncc）不随发布物分发，因此不在此列。
- **Python**：PyInstaller 冻结进 Core sidecar 的发行版，读自构建环境。
- **Rust**：Tauri 壳解析出的 crate 图。
- **资产与内联代码**：没有 lockfile 能描述它们，逐项人工确认。

## 需要单独注意的三项

1. **Live2D Cubism Core** 是专有软件，不是开源件。仓库的 LICENSE 完全不适用于它，它的再分发由 Live2D 自己的 SDK 许可证管辖。Joi 支持导入你自己的 Live2D 模型：本应用不为任何导入的模型授予权利，你导入的模型按其权利人的条款使用。
2. **默认角色的 Live2D 形象是 Live2D 官方示例模型「桃瀬ひより / Hiyori Momose」，版权归 Live2D Inc.**。示例数据条款允许 General User 与小规模企业免费用于商业与非商业用途，但要求**保留版权声明**、**不得改动角色设计**，且**不得作为发布者的原创角色呈现**。
3. **PySide6 / Qt 不在发布物里。** 它出现在 `requirements.txt`，但 sidecar 构建用 `--exclude-module PySide6` 明确排除，因此 LGPL/GPL 的 Qt 绑定**不随包分发**。本文件的 Python 段按构建实际排除项过滤，而不是按环境里装了什么。
4. **PyInstaller** 是 GPL-2.0-or-later，**但带 bootloader exception**——用它冻结出来的应用可以按任意许可证分发，包括闭源。这条例外是 Joi 能以非开源形式发布的前提之一。

## 许可证全文

本文件只列出组件、版本与许可证标识。各许可证全文随各依赖分发，位于安装后的包目录中；发布物按各许可证要求随包附带。

"""
    return header + "\n".join(sections)


if __name__ == "__main__":
    raise SystemExit(main())
