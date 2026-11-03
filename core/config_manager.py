# -*- coding: utf-8 -*-
"""普通配置管理：默认模板只读，本机配置独立读写。"""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from core.paths import get_base_dir

BASE_DIR = get_base_dir()
CONFIG_ROOT = BASE_DIR / "config"
DEFAULT_CONFIG_DIR = CONFIG_ROOT / "default"
LOCAL_CONFIG_DIR = CONFIG_ROOT / "local"
# 保留旧常量名，指向运行时配置目录，避免外部模块引用时改变语义。
CONFIG_DIR = LOCAL_CONFIG_DIR
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


def _deep_merge(defaults: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """递归补齐缺失默认项；已有本机值始终优先。"""
    result = copy.deepcopy(defaults)
    for key, value in values.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _normalize_config(name: str, data: dict[str, Any]) -> dict[str, Any]:
    """规范历史配置格式，统一粉丝牌开关为布尔值。"""
    if name == "music":
        requests_config = data.get("requests")
        if isinstance(requests_config, dict):
            value = requests_config.get("require_fans_medal", False)
            # 旧点歌配置：0=要求粉丝牌，1=不要求粉丝牌。
            if isinstance(value, bool):
                pass
            elif value == 0 or (isinstance(value, str) and value.strip() == "0"):
                requests_config["require_fans_medal"] = True
            elif value == 1 or (isinstance(value, str) and value.strip() == "1"):
                requests_config["require_fans_medal"] = False
            elif isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                requests_config["require_fans_medal"] = value.strip().lower() == "true"
    return data


def _read_json(path: Path, name: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"读取 {name}.json 失败：{exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{name}.json 顶层必须是 JSON 对象。")
    _assert_no_secrets(data)
    return data


class ConfigManager:
    """配置模板只读；所有运行时读写统一落在 config/local/。"""

    def __init__(
        self,
        directory: Path | None = None,
        default_directory: Path | None = None,
    ):
        self._managed_layout = directory is None
        if self._managed_layout:
            self.directory = LOCAL_CONFIG_DIR.resolve()
            self.default_directory = (default_directory or DEFAULT_CONFIG_DIR).resolve()
            # 兼容用户从旧版本升级、尚未迁移旧 config/*.json 的情况。
            self.legacy_directory: Path | None = CONFIG_ROOT.resolve()
        else:
            self.directory = Path(directory).resolve()
            self.default_directory = (
                Path(default_directory).resolve() if default_directory is not None else None
            )
            self.legacy_directory = None
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", name):
            raise ConfigError("配置名称无效。")
        path = (self.directory / f"{name}.json").resolve()
        if path.parent != self.directory:
            raise ConfigError("配置路径越界。")
        return path

    def _defaults(self, name: str, supplied: dict[str, Any] | None) -> dict[str, Any]:
        result = copy.deepcopy(supplied) if isinstance(supplied, dict) else {}
        if self.default_directory is not None:
            template = (self.default_directory / f"{name}.json").resolve()
            if template.parent == self.default_directory and template.is_file():
                result = _deep_merge(result, _read_json(template, name))
        return result

    def load(self, name: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        path = self._path(name)
        with _LOCK:
            default_data = self._defaults(name, defaults)
            source_path = path
            if not path.is_file() and self.legacy_directory is not None:
                legacy = (self.legacy_directory / f"{name}.json").resolve()
                if legacy.parent == self.legacy_directory and legacy.is_file():
                    source_path = legacy

            if source_path.is_file():
                current = _read_json(source_path, name)
            else:
                current = {}

            merged = _normalize_config(name, _deep_merge(default_data, current))
            _assert_no_secrets(merged)

            # 正常运行模式会创建本机配置，并将新增模板字段补齐到本机文件。
            if self._managed_layout and (not path.is_file() or merged != current):
                _write_json_atomic(path, merged)
            elif not self._managed_layout and not source_path.is_file():
                return copy.deepcopy(default_data)
            return merged

    def save(self, name: str, data: dict[str, Any]) -> Path:
        if not isinstance(data, dict):
            raise ConfigError("配置内容必须是 JSON 对象。")
        path = self._path(name)
        with _LOCK:
            normalized = _normalize_config(name, copy.deepcopy(data))
            if self._managed_layout:
                normalized = _deep_merge(self._defaults(name, None), normalized)
            _assert_no_secrets(normalized)
            _write_json_atomic(path, normalized)
        return path
