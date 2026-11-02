# -*- coding: utf-8 -*-
"""普通配置管理：只读写 config/，拒绝保存敏感凭据字段。"""
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
    r"(?:^|_)(?:cookie|sessdata|bili_jct|password|passwd|secret|token|api_key|access_key|private_key)(?:$|_)",
    re.IGNORECASE,
)


class ConfigError(ValueError):
    """配置文件内容或路径不合法。"""


def _assert_no_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key)
            normalized_name = re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", name).casefold()
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
        temporary = path.with_suffix(".json.tmp")
        encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        with _LOCK:
            try:
                temporary.write_text(encoded, encoding="utf-8")
                os.replace(temporary, path)
            finally:
                if temporary.exists():
                    temporary.unlink(missing_ok=True)
        return path

    def migrate_legacy_config(self, legacy_path: Path | None = None) -> dict[str, str]:
        """把旧 config.json 的普通字段拆分到新文件；不会复制任何凭据值。"""
        source = legacy_path or (BASE_DIR / "config.json")
        if not source.is_file():
            return {}
        try:
            legacy = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"旧配置读取失败：{exc}") from exc
        if not isinstance(legacy, dict):
            raise ConfigError("旧 config.json 顶层必须是 JSON 对象。")

        app = legacy.get("app") if isinstance(legacy.get("app"), dict) else {}
        bili = legacy.get("bilibili") if isinstance(legacy.get("bilibili"), dict) else {}
        live2d = legacy.get("live2d") if isinstance(legacy.get("live2d"), dict) else {}
        voice = legacy.get("tts") if isinstance(legacy.get("tts"), dict) else {}
        obs = legacy.get("obs_websocket") if isinstance(legacy.get("obs_websocket"), dict) else {}

        mapping = {
            "app": dict(app),
            "live": {
                "room_id": int(bili.get("room_id", 0) or 0),
                "reconnect": bool(bili.get("reconnect", True)),
                "reconnect_interval_seconds": max(2, int(bili.get("reconnect_interval_seconds", 5) or 5)),
            },
            "live2d": dict(live2d),
            "voice": dict(voice),
            "obs": {
                "host": str(obs.get("host", "127.0.0.1")),
                "port": int(obs.get("port", 4455) or 4455),
                "timeout": float(obs.get("timeout", 2.5) or 2.5),
                "password_file": str(obs.get("password_file", "credentials/obs.json")),
            },
        }
        migrated: dict[str, str] = {}
        for name, data in mapping.items():
            target = self._path(name)
            if target.exists():
                continue
            self.save(name, data)
            migrated[name] = str(target.relative_to(BASE_DIR))
        return migrated
