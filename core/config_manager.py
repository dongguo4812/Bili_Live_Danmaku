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
        """一次性迁移旧 config.json；旧配置仅作为初次导入来源，之后新配置优先。"""
        source = (legacy_path or (BASE_DIR / "config.json")).resolve()
        if not source.is_file():
            return {}

        # 标记放在 data/，不进入普通配置，也不会提交到 Git。
        marker = source.parent / "data" / "legacy_config_migrated_v1.json"
        if marker.is_file():
            return {}

        try:
            legacy = json.loads(source.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"旧 config.json 读取失败：{exc}") from exc
        if not isinstance(legacy, dict):
            raise ConfigError("旧 config.json 顶层必须是 JSON 对象。")

        app = legacy.get("app") if isinstance(legacy.get("app"), dict) else {}
        bili = legacy.get("bilibili") if isinstance(legacy.get("bilibili"), dict) else {}
        live2d = legacy.get("live2d") if isinstance(legacy.get("live2d"), dict) else {}
        old_voice = legacy.get("tts") if isinstance(legacy.get("tts"), dict) else {}
        obs = legacy.get("obs_websocket") if isinstance(legacy.get("obs_websocket"), dict) else {}

        # 旧 user_settings.json 保存的是用户最后确认的房间号，优先级高于旧 config.json。
        old_user_settings = source.parent / "user_settings.json"
        try:
            has_old_user_settings = old_user_settings.is_file() and isinstance(
                json.loads(old_user_settings.read_text(encoding="utf-8-sig")), dict
            )
        except Exception:
            has_old_user_settings = False

        try:
            legacy_room_id = max(0, int(bili.get("room_id", 0) or 0))
        except (TypeError, ValueError):
            legacy_room_id = 0

        mapping = {
            "app": dict(app),
            "live": {
                "room_id": 0 if has_old_user_settings else legacy_room_id,
                "reconnect": bool(bili.get("reconnect", True)),
                "reconnect_interval_seconds": max(
                    2, int(bili.get("reconnect_interval_seconds", 5) or 5)
                ),
                "connect_on_start": False,
            },
            "live2d": dict(live2d),
            # 新版界面从 voice.tts 读取播报偏好；旧版 config.json 的 tts 整体放入此节点。
            "voice": {"tts": dict(old_voice)} if old_voice else {},
            "obs": {
                "host": str(obs.get("host", "127.0.0.1")),
                "port": int(obs.get("port", 4455) or 4455),
                "timeout": float(obs.get("timeout", 2.5) or 2.5),
                # 不迁移旧明文密码或旧密码文件路径，统一要求写入 DPAPI 凭据库。
                "password_file": "credentials/obs.json",
            },
        }

        migrated: dict[str, str] = {}
        for name, legacy_data in mapping.items():
            if not legacy_data:
                continue
            current = self.load(name, {})
            merged = self._merge_legacy_values(current, legacy_data)
            if merged != current:
                self.save(name, merged)
                migrated[name] = str(self._path(name).relative_to(source.parent))

        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"source": source.name, "migrated": sorted(migrated)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return migrated

    @classmethod
    def _merge_legacy_values(cls, current: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
        """递归合并：初次升级时旧配置覆盖仓库默认值，其他新字段继续保留。"""
        merged = dict(current)
        for key, value in legacy.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = cls._merge_legacy_values(existing, value)
            else:
                merged[key] = value
        return merged
