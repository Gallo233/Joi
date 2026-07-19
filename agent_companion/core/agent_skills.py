from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable
from urllib.parse import urlparse
import uuid
import zipfile

import yaml

from agent_companion.core.collaboration_store import CollaborationStore


SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SUPPORTED_SCRIPT_SUFFIXES = {".py", ".js", ".mjs", ".sh"}
PACKAGE_FOLDERS = {"scripts", "references", "assets"}
MAX_FILES = 2_000
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PACKAGE_BYTES = 100 * 1024 * 1024
MAX_SKILL_MD_BYTES = 512 * 1024


class AgentSkillError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SkillInspection:
    source: str
    source_kind: str
    root_path: str
    name: str
    description: str
    version: str
    author: str
    license: str
    digest: str
    instructions: str
    scripts: tuple[str, ...]
    references: tuple[str, ...]
    assets: tuple[str, ...]
    permissions: dict[str, list[str]]
    dependencies: tuple[str, ...]
    warnings: tuple[str, ...]

    def payload(self, *, include_instructions: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "source_kind": self.source_kind,
            "root_path": self.root_path,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "license": self.license,
            "digest": self.digest,
            "scripts": list(self.scripts),
            "references": list(self.references),
            "assets": list(self.assets),
            "permissions": self.permissions,
            "dependencies": list(self.dependencies),
            "warnings": list(self.warnings),
            "code_bearing": bool(self.scripts),
            "implicit_invocation": False if self.scripts else True,
        }
        if include_instructions:
            result["instructions"] = self.instructions
        return result


class AgentSkillService:
    """Inspect, install and run Agent Skills without importing third-party code.

    Skill scripts are data until an explicit run request enters the external
    sandbox runner. They are never imported by Joi's Python process.
    """

    def __init__(self, workspace: Path, store: CollaborationStore) -> None:
        self.workspace = workspace.resolve()
        self.store = store
        self.install_root = store.skill_root.resolve()
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.run_root = (store.data_home / "skill_runs").resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)

    def inspect(self, source: str) -> dict[str, Any]:
        source = str(source or "").strip()
        if not source:
            raise AgentSkillError("source_required", "请选择 Skill 目录、ZIP 或 Git 仓库。")
        temporary: tempfile.TemporaryDirectory[str] | None = None
        try:
            root, source_kind, temporary = self._materialize_source(source)
            inspection = inspect_skill_root(root, source=source, source_kind=source_kind)
            return {"ok": True, "inspection": inspection.payload(include_instructions=True)}
        finally:
            if temporary is not None:
                temporary.cleanup()

    def install(self, source: str, *, scope: str = "global", scope_id: str = "", expected_digest: str = "") -> dict[str, Any]:
        scope = _valid_scope(scope)
        source = str(source or "").strip()
        temporary: tempfile.TemporaryDirectory[str] | None = None
        try:
            root, source_kind, temporary = self._materialize_source(source)
            inspection = inspect_skill_root(root, source=source, source_kind=source_kind)
            if expected_digest and expected_digest != inspection.digest:
                raise AgentSkillError("hash_changed", "Skill 内容已在预览后发生变化，请重新检查。")
            destination = self._destination(scope, scope_id, inspection.name)
            staging = destination.parent / f".{destination.name}.install-{uuid.uuid4().hex[:8]}"
            staging.parent.mkdir(parents=True, exist_ok=True)
            if staging.exists():
                shutil.rmtree(staging)
            _safe_copy_tree(root, staging)
            installed = inspect_skill_root(staging, source=source, source_kind=source_kind)
            if installed.digest != inspection.digest:
                shutil.rmtree(staging, ignore_errors=True)
                raise AgentSkillError("copy_verification_failed", "Skill 安装副本校验失败。")
            old = destination.parent / f".{destination.name}.old-{uuid.uuid4().hex[:8]}"
            if destination.exists():
                destination.replace(old)
            staging.replace(destination)
            shutil.rmtree(old, ignore_errors=True)
            manifest = installed.payload(include_instructions=False)
            row = self.store.upsert_skill_installation(
                {
                    "name": installed.name,
                    "version": installed.version,
                    "scope": scope,
                    "scope_id": _scope_id(scope, scope_id),
                    "source": source,
                    "root_path": str(destination),
                    "digest": installed.digest,
                    "manifest": manifest,
                    "enabled": True,
                }
            )
            return {"ok": True, "skill": row, "inspection": manifest}
        finally:
            if temporary is not None:
                temporary.cleanup()

    def list(self, *, project_id: str = "", character_id: str = "", include_disabled: bool = True) -> dict[str, Any]:
        rows = self.store.list_skill_installations(project_id, character_id, include_disabled)
        return {"ok": True, "skills": rows}

    def validate(self, installation_id: str) -> dict[str, Any]:
        row = self.store.skill_installation(installation_id)
        if not row:
            return {"ok": False, "error": "skill_not_found"}
        root = self._installed_root(row)
        try:
            current = inspect_skill_root(root, source=row["source"], source_kind="installed")
        except AgentSkillError as exc:
            return {"ok": False, "error": exc.code, "message": exc.message, "skill": row}
        unchanged = current.digest == row["digest"]
        return {
            "ok": unchanged,
            "error": "" if unchanged else "hash_changed",
            "skill": row,
            "inspection": current.payload(include_instructions=False),
        }

    def set_enabled(self, installation_id: str, enabled: bool) -> dict[str, Any]:
        return self.store.set_skill_enabled(installation_id, enabled)

    def update(self, installation_id: str, *, expected_digest: str = "") -> dict[str, Any]:
        row = self.store.skill_installation(installation_id)
        if not row:
            return {"ok": False, "error": "skill_not_found"}
        return self.install(
            row["source"],
            scope=row["scope"],
            scope_id=row["scope_id"],
            expected_digest=expected_digest,
        )

    def uninstall(self, installation_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        if not confirmed:
            return {"ok": False, "error": "confirmation_required"}
        row = self.store.skill_installation(installation_id)
        if not row:
            return {"ok": False, "error": "skill_not_found"}
        root = self._installed_root(row)
        if root.is_symlink():
            raise AgentSkillError("unsafe_install_path", "Skill 安装目录不能是符号链接。")
        if root.exists():
            shutil.rmtree(root)
        result = self.store.delete_skill_installation(installation_id)
        result["residuals"] = []
        return result

    def install_draft(self, draft_id: str, *, scope: str = "project", scope_id: str = "") -> dict[str, Any]:
        draft = self.store.get_skill_draft(draft_id)
        if not draft:
            return {"ok": False, "error": "draft_not_found"}
        if draft.get("status") not in {"draft", "approved"}:
            return {"ok": False, "error": "draft_not_installable"}
        payload = draft.get("payload") if isinstance(draft.get("payload"), dict) else {}
        body = str(payload.get("instructions") or payload.get("steps") or "").strip()
        if not body:
            return {"ok": False, "error": "draft_instructions_required"}
        slug = "joi-draft-" + hashlib.sha256(str(draft["id"]).encode("utf-8")).hexdigest()[:12]
        metadata = {
            "name": slug,
            "description": str(payload.get("description") or draft["name"])[:1_024],
            "version": "0.1.0",
            "author": "Joi + user",
            "license": "Private",
            "permissions": payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {},
        }
        with tempfile.TemporaryDirectory(prefix="joi-skill-draft-") as temporary:
            root = Path(temporary) / slug
            root.mkdir()
            frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
            (root / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")
            installed = self.install(str(root), scope=scope, scope_id=scope_id)
        if installed.get("ok"):
            self.store.update_skill_draft(draft_id, "installed")
        return installed

    def run(
        self,
        installation_id: str,
        *,
        script: str = "",
        arguments: Iterable[object] | None = None,
        permission_profile: str = "observe",
        approved: bool = False,
        project_root: str = "",
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        row = self.store.skill_installation(installation_id)
        if not row or not row.get("enabled"):
            return {"ok": False, "error": "skill_not_available"}
        validation = self.validate(installation_id)
        if not validation.get("ok"):
            return validation
        root = self._installed_root(row)
        inspection = inspect_skill_root(root, source=row["source"], source_kind="installed")
        if not script:
            return {
                "ok": True,
                "mode": "instructions",
                "skill": row,
                "instructions": inspection.instructions,
                "references": list(inspection.references),
            }
        clean_script = _safe_relative(script)
        if clean_script not in inspection.scripts:
            return {"ok": False, "error": "unknown_script"}
        if permission_profile == "observe":
            return {"ok": False, "error": "observe_session_cannot_run_scripts", "requires_approval": True}
        if not approved:
            return {
                "ok": False,
                "error": "script_approval_required",
                "requires_approval": True,
                "review": {
                    "source": row["source"],
                    "digest": row["digest"],
                    "script": clean_script,
                    "permissions": inspection.permissions,
                    "timeout_seconds": max(1, min(int(timeout_seconds), 900)),
                    "network": "disabled",
                },
            }
        script_path = (root / clean_script).resolve()
        _require_inside(root, script_path)
        project = Path(project_root).expanduser().resolve() if project_root else self.workspace
        if not project.is_dir():
            return {"ok": False, "error": "project_root_not_found"}
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        run_dir = self.run_root / run_id
        command = _sandbox_command(script_path, project, root, run_dir, arguments or [])
        if not command:
            return {"ok": False, "error": "sandbox_runner_unavailable"}
        run_dir.mkdir(parents=True, exist_ok=False)
        env = {"PATH": os.environ.get("PATH", ""), "LANG": os.environ.get("LANG", "en_US.UTF-8"), "HOME": str(run_dir)}
        started = time.time()
        try:
            completed = subprocess.run(
                command,
                cwd=str(project),
                env=env,
                input="",
                text=True,
                capture_output=True,
                timeout=max(1, min(int(timeout_seconds), 900)),
            )
            result = {
                "ok": completed.returncode == 0,
                "run_id": run_id,
                "returncode": completed.returncode,
                "stdout": completed.stdout[-20_000:],
                "stderr": completed.stderr[-20_000:],
                "duration_ms": round((time.time() - started) * 1000, 1),
                "sandboxed": True,
                "network": "disabled",
            }
        except subprocess.TimeoutExpired as exc:
            result = {
                "ok": False,
                "error": "skill_run_timeout",
                "run_id": run_id,
                "stdout": str(exc.stdout or "")[-20_000:],
                "stderr": str(exc.stderr or "")[-20_000:],
                "duration_ms": round((time.time() - started) * 1000, 1),
                "sandboxed": True,
                "network": "disabled",
            }
        recorded = self.store.record_skill_run(
            row["id"],
            "completed" if result.get("ok") else "failed",
            json.dumps(
                {
                    "external_run_id": run_id,
                    "script": clean_script,
                    "permission_profile": permission_profile,
                    "result": result,
                },
                ensure_ascii=False,
            ),
        )
        if recorded.get("run_id"):
            result["record_id"] = recorded["run_id"]
        return result

    def _materialize_source(self, source: str) -> tuple[Path, str, tempfile.TemporaryDirectory[str] | None]:
        path = Path(source).expanduser()
        if path.exists():
            if path.is_dir():
                return _find_skill_root(path.resolve()), "directory", None
            if path.is_file() and path.suffix.casefold() == ".zip":
                temporary = tempfile.TemporaryDirectory(prefix="joi-skill-")
                extracted = Path(temporary.name)
                try:
                    _safe_extract_zip(path.resolve(), extracted)
                    return _find_skill_root(extracted), "zip", temporary
                except Exception:
                    temporary.cleanup()
                    raise
            raise AgentSkillError("unsupported_source", "仅支持 Skill 目录或 ZIP 文件。")
        parsed = urlparse(source)
        if parsed.scheme not in {"https", "ssh"} and not source.startswith("git@"):
            raise AgentSkillError("source_not_found", "没有找到这个 Skill 来源。")
        git = shutil.which("git")
        if not git:
            raise AgentSkillError("git_unavailable", "本机未安装 Git。")
        temporary = tempfile.TemporaryDirectory(prefix="joi-skill-git-")
        destination = Path(temporary.name) / "repo"
        try:
            completed = subprocess.run(
                [git, "clone", "--depth", "1", "--no-recurse-submodules", source, str(destination)],
                capture_output=True,
                text=True,
                timeout=60,
                env={"PATH": os.environ.get("PATH", ""), "GIT_TERMINAL_PROMPT": "0"},
            )
        except subprocess.TimeoutExpired as exc:
            temporary.cleanup()
            raise AgentSkillError("git_timeout", "Git 仓库读取超时。") from exc
        if completed.returncode != 0:
            temporary.cleanup()
            raise AgentSkillError("git_clone_failed", "Git 仓库无法读取。")
        shutil.rmtree(destination / ".git", ignore_errors=True)
        return _find_skill_root(destination), "git", temporary

    def _destination(self, scope: str, scope_id: str, name: str) -> Path:
        safe_scope_id = _component(_scope_id(scope, scope_id) or "all")
        destination = (self.install_root / scope / safe_scope_id / name).resolve()
        _require_inside(self.install_root, destination)
        return destination

    def _installed_root(self, row: dict[str, Any]) -> Path:
        root = Path(str(row.get("root_path") or "")).resolve()
        _require_inside(self.install_root, root)
        return root


def inspect_skill_root(root: Path, *, source: str, source_kind: str) -> SkillInspection:
    root = root.resolve()
    if not root.is_dir():
        raise AgentSkillError("skill_root_not_found", "Skill 根目录不存在。")
    skill_md = root / "SKILL.md"
    if not skill_md.is_file() or skill_md.is_symlink():
        raise AgentSkillError("skill_md_missing", "Skill 必须包含常规文件 SKILL.md。")
    if skill_md.stat().st_size > MAX_SKILL_MD_BYTES:
        raise AgentSkillError("skill_md_too_large", "SKILL.md 体积过大。")
    raw = skill_md.read_text(encoding="utf-8")
    metadata, instructions = _frontmatter(raw)
    name = str(metadata.get("name") or "").strip()
    description = str(metadata.get("description") or "").strip()
    if not name or len(name) > 64 or not SKILL_NAME.fullmatch(name):
        raise AgentSkillError("invalid_skill_name", "Skill name 必须是 1–64 位小写字母、数字和连字符。")
    if not description or len(description) > 1_024:
        raise AgentSkillError("invalid_skill_description", "Skill description 必须填写且不超过 1024 字符。")
    files = _scan_package(root)
    scripts = tuple(path for path in files if path.startswith("scripts/"))
    unknown_scripts = [path for path in scripts if Path(path).suffix.casefold() not in SUPPORTED_SCRIPT_SUFFIXES]
    if unknown_scripts:
        raise AgentSkillError("unknown_script_type", f"不支持的脚本类型：{', '.join(unknown_scripts[:4])}")
    warnings: list[str] = []
    if scripts:
        warnings.append("包含脚本：安装后不会隐式调用，首次运行必须审核并进入独立沙箱。")
    license_name = str(metadata.get("license") or "").strip()
    if not license_name:
        warnings.append("未声明许可证。")
    permissions = _permissions(metadata)
    return SkillInspection(
        source=source,
        source_kind=source_kind,
        root_path=str(root),
        name=name,
        description=description,
        version=str(metadata.get("version") or "0.0.0")[:64],
        author=str(metadata.get("author") or "")[:200],
        license=license_name[:120],
        digest=_digest(root, files),
        instructions=instructions.strip(),
        scripts=scripts,
        references=tuple(path for path in files if path.startswith("references/")),
        assets=tuple(path for path in files if path.startswith("assets/")),
        permissions=permissions,
        dependencies=_strings(metadata.get("dependencies"), 100),
        warnings=tuple(warnings),
    )


def _frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        raise AgentSkillError("frontmatter_missing", "SKILL.md 必须以 YAML frontmatter 开头。")
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AgentSkillError("frontmatter_missing", "SKILL.md frontmatter 格式不正确。")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise AgentSkillError("frontmatter_unclosed", "SKILL.md frontmatter 没有结束标记。") from exc
    try:
        parsed = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise AgentSkillError("frontmatter_invalid", "SKILL.md frontmatter 无法解析。") from exc
    if not isinstance(parsed, dict):
        raise AgentSkillError("frontmatter_invalid", "SKILL.md frontmatter 必须是对象。")
    return parsed, "\n".join(lines[end + 1 :])


def _permissions(metadata: dict[str, Any]) -> dict[str, list[str]]:
    raw = metadata.get("permissions")
    if not isinstance(raw, dict):
        joi = metadata.get("joi") if isinstance(metadata.get("joi"), dict) else {}
        raw = joi.get("permissions") if isinstance(joi.get("permissions"), dict) else {}
    return {
        key: _strings(raw.get(key), 100)
        for key in ("directories", "domains", "applications", "tools")
        if _strings(raw.get(key), 100)
    }


def _scan_package(root: Path) -> list[str]:
    files: list[str] = []
    total = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if any(part.startswith(".") and part not in {".well-known"} for part in PurePosixPath(relative).parts):
            continue
        if path.is_symlink():
            raise AgentSkillError("symlink_not_allowed", f"Skill 不能包含符号链接：{relative}")
        if path.is_dir():
            continue
        _require_inside(root, path.resolve())
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise AgentSkillError("file_too_large", f"Skill 文件过大：{relative}")
        total += size
        if total > MAX_PACKAGE_BYTES:
            raise AgentSkillError("package_too_large", "Skill 包总体积超过限制。")
        files.append(relative)
        if len(files) > MAX_FILES:
            raise AgentSkillError("too_many_files", "Skill 包文件数量超过限制。")
    return files


def _digest(root: Path, files: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with (root / relative).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _safe_extract_zip(source: Path, destination: Path) -> None:
    total = 0
    count = 0
    try:
        archive = zipfile.ZipFile(source)
    except (OSError, zipfile.BadZipFile) as exc:
        raise AgentSkillError("invalid_zip", "Skill ZIP 无法读取。") from exc
    with archive:
        for info in archive.infolist():
            relative = _safe_relative(info.filename)
            if not relative:
                continue
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise AgentSkillError("symlink_not_allowed", f"ZIP 不能包含符号链接：{relative}")
            if info.file_size > MAX_FILE_BYTES:
                raise AgentSkillError("file_too_large", f"ZIP 文件过大：{relative}")
            total += info.file_size
            count += 1
            if total > MAX_PACKAGE_BYTES or count > MAX_FILES:
                raise AgentSkillError("package_too_large", "Skill ZIP 超过大小或文件数限制。")
            target = (destination / relative).resolve()
            _require_inside(destination.resolve(), target)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source_handle, target.open("wb") as target_handle:
                shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)


def _find_skill_root(root: Path) -> Path:
    if (root / "SKILL.md").is_file():
        return root
    candidates = [path.parent for path in root.glob("*/SKILL.md") if path.is_file()]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise AgentSkillError("skill_md_missing", "来源中没有找到 SKILL.md。")
    raise AgentSkillError("multiple_skills", "来源包含多个 Skill，请选择具体目录。")


def _safe_copy_tree(source: Path, destination: Path) -> None:
    _scan_package(source)
    destination.mkdir(parents=True, exist_ok=False)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part.startswith(".") and part not in {".well-known"} for part in relative.parts):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=False)


def _sandbox_command(script: Path, project: Path, skill_root: Path, run_home: Path, arguments: Iterable[object]) -> list[str]:
    sandbox_exec = Path("/usr/bin/sandbox-exec")
    if not sandbox_exec.is_file() or sys.platform != "darwin":
        return []
    suffix = script.suffix.casefold()
    interpreter = {".py": sys.executable, ".js": shutil.which("node"), ".mjs": shutil.which("node"), ".sh": shutil.which("bash")}.get(suffix)
    if not interpreter:
        return []
    safe_args = [str(value)[:2_000] for value in arguments][:64]
    writable = (project, run_home, Path("/tmp"), Path("/private/tmp"), Path("/dev"))
    write_rules = " ".join(f'(subpath "{_seatbelt_escape(str(path))}")' for path in writable)
    profile = f"(version 1)(deny default)(allow process*)(allow sysctl-read)(allow mach-lookup)(allow file-read*)(allow file-write* {write_rules})(deny network*)"
    return [
        str(sandbox_exec),
        "-p",
        profile,
        interpreter,
        str(script),
        *safe_args,
    ]


def _seatbelt_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _valid_scope(value: str) -> str:
    if value not in {"global", "project", "character"}:
        raise AgentSkillError("invalid_scope", "Skill 可见范围必须是全局、项目或角色。")
    return value


def _scope_id(scope: str, value: str) -> str:
    if scope == "global":
        return ""
    result = _component(value)
    if not result:
        raise AgentSkillError("scope_id_required", "项目或角色 Skill 必须指定范围。")
    return result


def _component(value: object) -> str:
    text = str(value or "").strip()
    return "".join(char for char in text if char.isalnum() or char in "._-")[:120]


def _safe_relative(value: object) -> str:
    text = str(value or "").replace("\\", "/").strip("/")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        if not text:
            return ""
        raise AgentSkillError("path_traversal", "Skill 包含不安全路径。")
    return path.as_posix()


def _require_inside(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise AgentSkillError("path_traversal", "Skill 路径越过允许范围。") from exc


def _strings(value: object, limit: int) -> list[str]:
    rows = value if isinstance(value, (list, tuple)) else ([value] if value else [])
    return [str(row).strip()[:300] for row in rows if str(row).strip()][:limit]
