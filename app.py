from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from mvp.config import load_app_config
from mvp.settings_center import SettingsCenter
from mvp.source_layout import export_source_like_layout
from mvp.ui import ChatWindow


def main() -> int:
    root = Path(__file__).resolve().parent
    config = load_app_config(root / "config.yaml")

    app = QApplication(sys.argv)
    app.setApplicationName(config.app.title)

    export_source_like_layout(config)

    if "--chat" in sys.argv:
        window = ChatWindow(config)
        window.resize(900, 680)
    else:
        window = SettingsCenter(root)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
