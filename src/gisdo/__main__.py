"""``python -m gisdo`` 入口，等价于 CLI。"""

from gisdo.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
