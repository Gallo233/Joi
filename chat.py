from __future__ import annotations

import sys

import app


if __name__ == "__main__":
    if "--chat" not in sys.argv:
        sys.argv.append("--chat")
    raise SystemExit(app.main())
