"""PyInstaller entry point: double-click opens the GUI; hidden flags are used internally."""
from archforge.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
