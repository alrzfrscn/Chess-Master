"""Allow ``python -m chess_master``."""

from chess_master.app import main

if __name__ == "__main__":
    raise SystemExit(main())
