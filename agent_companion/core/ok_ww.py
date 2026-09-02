from __future__ import annotations

import os
from pathlib import Path
import sys


OK_WW_RUNNER_ENV = "OK_WW_RUNNER"


def ok_ww_runner_path() -> Path | None:
    """Resolve the user's OK-WW runner script, or None when it is unusable.

    There is deliberately no built-in default.  OK-WW is a separate Windows
    project that each user installs wherever they keep it, so baking one
    machine's layout into the source both ships that user's home directory in
    every build and makes readiness checks lie on every other machine.
    """
    configured = str(os.environ.get(OK_WW_RUNNER_ENV) or "").strip()
    if not configured:
        return None
    try:
        candidate = Path(configured).expanduser()
    except (OSError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def ok_ww_setup_hint() -> str:
    """Name the one thing standing between the user and a working adapter."""
    if sys.platform != "win32":
        return "OK-WW 只支持 Windows，当前系统无法运行这个适配器。"
    if not str(os.environ.get(OK_WW_RUNNER_ENV) or "").strip():
        return f"设置环境变量 {OK_WW_RUNNER_ENV} 指向本机的 OK-WW runner 脚本（run_ok_ww.ps1）。"
    return f"{OK_WW_RUNNER_ENV} 指向的脚本不存在，请检查路径是否正确。"
