from __future__ import annotations

from pathlib import Path

from app2.ui import run_app
from mvp.config import load_app_config


def main() -> int:
    root = Path(__file__).resolve().parent
    config = load_app_config(root / "config.yaml")
    return run_app(config)


if __name__ == "__main__":
    raise SystemExit(main())

