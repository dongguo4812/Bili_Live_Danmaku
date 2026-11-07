# -*- coding: utf-8 -*-
"""LOL Hex 启动引导：在主模块导入和运行前后捕获启动异常。"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path


def _extension_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _write_startup_error(message: str) -> None:
    """异常时追加独立诊断记录；日志目录不可用时回退到扩展根目录。"""
    root = _extension_root()
    content = (
        f"\n{'=' * 72}\n"
        f"{datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        f"{message.rstrip()}\n"
    )
    candidates = (root / "logs" / "startup-error.log", root / "lol_hex_startup_error.log")
    last_error = None
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 1024 * 1024:
                backup = path.with_suffix(path.suffix + ".1")
                try:
                    backup.unlink(missing_ok=True)
                    path.replace(backup)
                except OSError:
                    path.write_text("", encoding="utf-8")
            with path.open("a", encoding="utf-8") as stream:
                stream.write(content)
            return
        except OSError as exc:
            last_error = exc
    try:
        sys.stderr.write(f"LOL Hex 启动诊断文件写入失败：{last_error}\n{content}")
    except Exception:
        pass


def main() -> int:
    try:
        # import 放在捕获范围内，确保第三方依赖缺失或模块导入异常也能落盘。
        import main as hex_main

        if not hex_main.check_admin_privileges():
            return 0
        result = hex_main.main()
        return int(result or 0)
    except SystemExit as exc:
        code = exc.code
        if code not in (None, 0, 17):
            _write_startup_error(f"LOL Hex 通过 SystemExit 异常退出，退出码：{code}")
        return int(code) if isinstance(code, int) else (0 if code is None else 1)
    except BaseException:
        _write_startup_error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
