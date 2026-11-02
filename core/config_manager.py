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
    r"(?:^|_)(?:cookie|sessdata|bili_jct|password|passwd|secret|token|api_key|access_key|private_key|authorization)(?:$|_)",
    re.IGNORECASE,
)

# 必须与仓库随附的 config/*.json 初始模板保持一致。
# 只有当前值仍等于这些默认值时，旧配置才可以覆盖它；用户已修改的值优先保留。
SHIPPED_DEFAULTS: dict[str, dict[str, Any]] = {
    "app": {"host": "127.0.0.1", "port": 8768, "debug": False},
    "live": {
        "room_id": 0, "reconnect": True,
        "reconnect_interval_seconds": 5, "connect_on_start": False,
    },
    "live2d": {
        "format": "MOC2", "default_model": "live_uu",
        "model_dir": "models/live_uu", "model_config": "models/live_uu/model.json",
        "canvas_width": 1920, "canvas_height": 1080, "max_device_pixel_ratio": 2,
    },
    "voice": {
        "enabled": True, "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "+0%", "volume": "+0%",
    },
    "obs": {
        "host": "127.0.0.1", "port": 4455, "timeout": 2.5,
        "password_file": "credentials/obs.json", "source_name": "b站视频播放器",
    },
}


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

    def migrate_legacy_config(self, legacy_path: Path | None = None) -> dict[str, str]:
        """安全迁移旧 config.json；保留用户新配置，并支持中断后续跑。"""
        source = (legacy_path or (BASE_DIR / "config.json")).resolve()
        if not source.is_file():
            return {}

        marker = self.directory / ".legacy_config_migrated_v1.json"
        journal = self.directory / ".legacy_config_migration_v1.json"
        with _LOCK:
            if marker.is_file():
                return {}

            if journal.is_file():
                try:
                    plan = json.loads(journal.read_text(encoding="utf-8-sig"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ConfigError(f"旧配置迁移记录读取失败：{exc}") from exc
                if not isinstance(plan, dict) or plan.get("source") != str(source) or not isinstance(plan.get("entries"), list):
                    raise ConfigError("旧配置迁移记录无效；为避免覆盖配置，已停止迁移。")
            else:
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

                old_user_settings = source.parent / "user_settings.json"
                try:
                    old_settings_data = (
                        json.loads(old_user_settings.read_text(encoding="utf-8-sig"))
                        if old_user_settings.is_file() else {}
                    )
                    old_bilibili = old_settings_data.get("bilibili", {}) if isinstance(old_settings_data, dict) else {}
                    has_old_user_room = (
                        isinstance(old_bilibili, dict)
                        and int(old_bilibili.get("room_id", 0) or 0) > 0
                    )
                except (OSError, ValueError, TypeError):
                    has_old_user_room = False

                try:
                    legacy_room_id = max(0, int(bili.get("room_id", 0) or 0))
                except (TypeError, ValueError):
                    legacy_room_id = 0

                mapping = {
                    "app": dict(app),
                    "live": {
                        "room_id": 0 if has_old_user_room else legacy_room_id,
                        "reconnect": bool(bili.get("reconnect", True)),
                        "reconnect_interval_seconds": max(
                            2, int(bili.get("reconnect_interval_seconds", 5) or 5)
                        ),
                        "connect_on_start": False,
                    },
                    "live2d": dict(live2d),
                    "voice": dict(old_voice),
                    "obs": {
                        "host": str(obs.get("host", "127.0.0.1")),
                        "port": int(obs.get("port", 4455) or 4455),
                        "timeout": float(obs.get("timeout", 2.5) or 2.5),
                        # 旧明文密码及旧密码文件路径不进入普通配置。
                        "password_file": "credentials/obs.json",
                    },
                }

                entries = []
                for name, legacy_data in mapping.items():
                    if not legacy_data:
                        continue
                    current = self.load(name, {})
                    merged = self._merge_legacy_values(
                        current, legacy_data, SHIPPED_DEFAULTS.get(name, {})
                    )
                    _assert_no_secrets(merged)
                    if merged != current:
                        entries.append({"name": name, "before": current, "after": merged})
                plan = {"source": str(source), "entries": entries}
                _write_json_atomic(journal, plan)

            migrated: dict[str, str] = {}
            for entry in plan["entries"]:
                if not isinstance(entry, dict):
                    raise ConfigError("旧配置迁移记录条目无效。")
                name = entry.get("name")
                before = entry.get("before")
                after = entry.get("after")
                if not isinstance(name, str) or not isinstance(before, dict) or not isinstance(after, dict):
                    raise ConfigError("旧配置迁移记录条目无效。")
                current = self.load(name, {})
                merged, changed = self._apply_migration_plan(current, before, after)
                if changed:
                    self.save(name, merged)
                if merged != current or current == after:
                    migrated[name] = str(self._path(name).relative_to(source.parent))

            _write_json_atomic(marker, {
                "source": source.name,
                "migrated": sorted(migrated),
            })
            journal.unlink(missing_ok=True)
            return migrated

    @classmethod
    def _merge_legacy_values(
        cls,
        current: dict[str, Any],
        legacy: dict[str, Any],
        defaults: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """旧值只覆盖仍为仓库默认值的字段；已修改字段和新字段优先保留。"""
        defaults = defaults or {}
        merged = dict(current)
        for key, value in legacy.items():
            if key not in merged:
                merged[key] = value
                continue
            existing = merged[key]
            default_value = defaults.get(key, object())
            if isinstance(existing, dict) and isinstance(value, dict):
                nested_defaults = default_value if isinstance(default_value, dict) else {}
                merged[key] = cls._merge_legacy_values(existing, value, nested_defaults)
            elif default_value is not _MISSING and existing == default_value:
                merged[key] = value
        return merged

    @classmethod
    def _apply_migration_plan(
        cls,
        current: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """恢复迁移计划：只应用仍保持迁移前值的字段，保留计划生成后的用户编辑。"""
        result = dict(current)
        changed = False
        for key, target in after.items():
            if key not in before:
                if key not in result:
                    result[key] = target
                    changed = True
                elif isinstance(result[key], dict) and isinstance(target, dict):
                    nested, nested_changed = cls._apply_migration_plan(result[key], {}, target)
                    if nested_changed:
                        result[key] = nested
                        changed = True
                continue

            original = before[key]
            existing = result.get(key, _MISSING)
            if isinstance(original, dict) and isinstance(target, dict) and isinstance(existing, dict):
                nested, nested_changed = cls._apply_migration_plan(existing, original, target)
                if nested_changed:
                    result[key] = nested
                    changed = True
            elif existing is _MISSING or existing == original:
                if existing != target:
                    result[key] = target
                    changed = True
        return result, changed


_MISSING = object()
