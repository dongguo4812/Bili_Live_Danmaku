# -*- coding: utf-8 -*-
"""LOL Hex 启动入口。"""
from __future__ import annotations



def main() -> int:
    import main as hex_main

    if not hex_main.check_admin_privileges():
        return 0

    result = hex_main.main()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
