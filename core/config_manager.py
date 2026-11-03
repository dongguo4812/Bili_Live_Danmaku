# -*- coding: utf-8 -*-
"""普通配置管理：只读写 config/，拒绝保存敏感凭据。"""
from __future__ import annotations

import json
import os
import re
import threading

from core.paths import get_base_dir
from pathlib import Path
from typing import Any

BASE_DIR = get_base_dir()
CONFIG_DIR = BASE_DIR / "config"
_LOCK = threading.RLock()
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:cookie|sess_?data|bili_jct|buvid[34]|ac_time_value|password|passwd|secret|token|api_key|access_key|private_key|authorization)(?:$|_)",
    re.IGNORECASE,
)

class ConfigError(ValueError):
    """配置文件内容或路径不合法。"""


def _assert_no_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            normalized_name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
            normalized_name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", normalized_name).casefold()
            # 文件路径字段仅表示凭据存放位置，不包含凭据值本身。
            if normalized_name.endswith(("_file", "_path")):
                if isinstance(child, (dict, list)):
                    _assert_no_secrets(child, f"{path}.{name}")
                continue
            if _SECRET_KEY.search(normalized_name):
                raise ConfigError(f"普通配置禁止包含敏感字段：{path}.{name}")
            _assert_no_secrets(child, f"{path}.{name}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_secrets(child, f"{path}[{index}]")


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


class ConfigManager:
    """模块独立配置读写器。文件名必须是简单 JSON 文件名。"""

    def __init__(self, directory: Path | None = None):
        self.directory = (directory or CONFIG_DIR).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", name):
            raise ConfigError("配置名称无效。")
        path = (self.directory / f"{name}.json").resolve()
        if path.parent != self.directory:
            raise ConfigError("配置路径越界。")
        return path

    def load(self, name: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        path = self._path(name)
        with _LOCK:
            if not path.is_file():
                return dict(defaults or {})
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ConfigError(f"读取 {name}.json 失败：{exc}") from exc
            if not isinstance(data, dict):
                raise ConfigError(f"{name}.json 顶层必须是 JSON 对象。")
            _assert_no_secrets(data)
            return data

    def save(self, name: str, data: dict[str, Any]) -> Path:
        if not isinstance(data, dict):
            raise ConfigError("配置内容必须是 JSON 对象。")
        _assert_no_secrets(data)
        path = self._path(name)
        with _LOCK:
            _write_json_atomic(path, data)
        return path

