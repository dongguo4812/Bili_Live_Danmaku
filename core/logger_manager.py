# -*- coding: utf-8 -*-
"""统一日志管理：按日期分文件、按配置保留，并对常见凭据字段脱敏。"""
from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from core.config_manager import ConfigManager

BASE_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = BASE_DIR / "logs"

_SECRET_VALUE_PATTERN = re.compile(
    r'(?i)(SESSDATA|bili_jct|buvid3|buvid4|ac_time_value|DedeUserID|cookie|password|passwd|token|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token)(\\s*[:=]\\s*)(["\\']?)([^\\s,;}"\\']+)',
    re.IGNORECASE,
)
_COOKIE_HEADER_PATTERN = re.compile(r'(?i)(Cookie\\s*:\\s*)([^\\r\\n]+)', re.IGNORECASE)


def _redact(message: str) -> str:
    result = _SECRET_VALUE_PATTERN.sub(
        lambda match: match.group(1) + match.group(2) + "***", str(message)
    )
    return _COOKIE_HEADER_PATTERN.sub(lambda match: match.group(1) + "***", result)


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = _redact(record.getMessage())
            record.args = ()
        except Exception:
            record.msg = "[log message redacted]"
            record.args = ()
        return True


class DailyFileHandler(logging.Handler):
    """每天写入独立的 YYYY-MM-DD.log 文件。"""

    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._current_date = date.today()
        self._stream = None
        self._open_stream()

    def _open_stream(self) -> None:
        if self._stream:
            self._stream.close()
        path = self.directory / f"{self._current_date.isoformat()}.log"
        self._stream = path.open("a", encoding="utf-8", buffering=1)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            today = date.today()
            if today != self._current_date:
                self._current_date = today
                self._open_stream()
            message = self.format(record)
            self._stream.write(message + "\n")
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._stream:
                self._stream.close()
                self._stream = None
        finally:
            super().close()


def _cleanup_old_logs(retention_days: int) -> None:
    if retention_days <= 0:
        return
    cutoff = date.today() - timedelta(days=retention_days - 1)
    for path in LOG_DIR.glob("*.log"):
        try:
            parsed = date.fromisoformat(path.stem)
            if parsed < cutoff:
                path.unlink(missing_ok=True)
        except (ValueError, OSError):
            continue


def setup_logging() -> None:
    try:
        config: dict[str, Any] = ConfigManager().load(
            "logging", {"level": "INFO", "retention_days": 7, "daily_rotation": True}
        )
        level_name = str(config.get("level", "INFO")).upper()
        level = getattr(logging, level_name, logging.INFO)
        retention = max(0, int(config.get("retention_days", 7) or 0))
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _cleanup_old_logs(retention)

        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        redactor = SecretRedactionFilter()
        handlers: list[logging.Handler] = [logging.StreamHandler(), DailyFileHandler(LOG_DIR)]
        for handler in handlers:
            handler.setFormatter(formatter)
            handler.addFilter(redactor)

        root = logging.getLogger()
        for old_handler in list(root.handlers):
            root.removeHandler(old_handler)
            try:
                old_handler.close()
            except Exception:
                pass
        root.setLevel(level)
        for handler in handlers:
            root.addHandler(handler)
    except Exception:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
        logging.getLogger(__name__).exception("统一日志初始化失败，已回退到控制台日志。")
