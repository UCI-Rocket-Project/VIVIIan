from __future__ import annotations

from ucirplgui.backend.pipeline import main as run_backend_main


def main() -> int:
    run_backend_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
