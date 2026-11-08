# -*- coding: utf-8 -*-
"""Bili Live Danmaku application entry point."""
from __future__ import annotations

import atexit
import asyncio
import ctypes
import base64
import hashlib
import json
import logging
import math
import os
import re
import socket
import threading
import time
import tempfile
import uuid
from urllib.parse import urlparse

import websocket
from collections import deque
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory

from bilibili_client import BilibiliClientError, LiveRoomClient, QRLogin, validate_cookie_file
from core.paths import get_base_dir
from core.config_manager import ConfigManager
from core.credential_store import CredentialStore
from core.application import ApplicationRuntime
from core.extension_manager import LolHexExtensionManager
from core.logger_manager import setup_logging
from modules.music.service import MusicModuleFacade, get_lx_status, is_lx_playing
from modules.video.service import VideoModuleFacade
from modules.live2d.service import Live2DModule
from modules.voice.service import VoiceModule

BASE_DIR = get_base_dir()
MODEL_ROOT = BASE_DIR / "models"
DISPLAY_WIDGET_TYPES = {"live2d", "voice", "music", "music_widget", "video", "danmaku", "lol_hex_bar", "lol_hex_display", "clock"}
TTS_CACHE_DIR = Path(tempfile.gettempdir()) / "bili_live_danmaku_tts"
DEFAULT_MODEL = "live_uu"
TTS_VOICES = [
    "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunjianNeural",
    "zh-CN-XiaoyiNeural", "zh-CN-YunyangNeural", "zh-CN-XiaochenNeural",
    "zh-CN-XiaohanNeural", "zh-CN-XiaomengNeural", "zh-CN-XiaomoNeural",
    "zh-CN-XiaoruiNeural", "zh-CN-XiaoshuangNeural",
]

setup_logging()
logger = logging.getLogger("bili_live_danmaku")
APPLICATION_RUNTIME = ApplicationRuntime()

RUNTIME_STATE_LOCK = threading.Lock()
SETTINGS_LOCK = threading.RLock()
BILIBILI_STATE_LOCK = threading.RLock()
DANMAKU_LOCK = threading.Lock()
RUNTIME_STATE: dict[str, str] = {
    "state": "not_started",
    "message": "等待 OBS 展示页面加载 Live2D 模型。",
}
BILIBILI_STATE: dict[str, Any] = {
    "logged_in": False,
    "connected": False,
    "connection_state": "disconnected",
    "message": "请先使用 B 站二维码登录。",
    "room_id": 0,
    "username": "",
}
DANMAKU_ITEMS: deque[dict[str, Any]] = deque(maxlen=300)
DANMAKU_SEQUENCE = 0

VOICE_PLAYBACK_STATE_LOCK = threading.Lock()
VOICE_TEST_QUEUE: deque[dict[str, str]] = deque(maxlen=50)
VOICE_PLAYBACK_STATE: dict[str, Any] = {
    "current": "",
    "queue": [],
    "speaking": False,
    "level": 0.0,
    "updated_at": 0.0,
}


def load_config() -> dict[str, Any]:
    """从模块独立配置文件组装运行时兼容配置。"""
    manager = ConfigManager()
    app_config = manager.load("app", {"host": "127.0.0.1", "port": 8768, "debug": False})
    live_config = manager.load("live", {
        "room_id": 0, "reconnect": True, "reconnect_interval_seconds": 5,
    })
    live2d_config = manager.load("live2d", {"format": "MOC2", "default_model": DEFAULT_MODEL, "enabled": True})
    voice_config = manager.load("voice", {})
    obs_config = manager.load("obs", {
        "host": "127.0.0.1", "port": 4455, "timeout": 2.5,
    })
    return {
        "app": app_config,
        "bilibili": {
            "room_id": int(live_config.get("room_id", 0) or 0),
            "credential_file": "credentials/bilibili.json",
            "reconnect": bool(live_config.get("reconnect", True)),
            "reconnect_interval_seconds": int(live_config.get("reconnect_interval_seconds", 5) or 5),
        },
        "live2d": live2d_config,
        "tts": voice_config,
        "obs_websocket": obs_config,
    }


CONFIG = load_config()
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
LOL_HEX_EXTENSION = LolHexExtensionManager(BASE_DIR, logger)


def _credential_path() -> Path:
    configured = CONFIG.get("bilibili", {}).get("credential_file", "credentials/bilibili.json")
    candidate = (BASE_DIR / str(configured)).resolve()
    try:
        candidate.relative_to(BASE_DIR.resolve())
    except ValueError as exc:
        raise ValueError("credential_file 必须位于项目目录内。") from exc
    return candidate


QR_LOGIN = QRLogin(_credential_path())
LIVE_CLIENT = LiveRoomClient(
    on_event=lambda item: _dispatch_live_event(item),
    on_state=lambda state, message: _set_bilibili_connection_state(state, message),
    reconnect=bool(CONFIG.get("bilibili", {}).get("reconnect", True)),
    reconnect_interval=int(CONFIG.get("bilibili", {}).get("reconnect_interval_seconds", 5)),
)


def _normalize_live2d_version(version: Any = "moc2") -> str:
    """规范化 Live2D 版本标识。"""
    value = str(version or "moc2").strip().lower()
    aliases = {"2": "moc2", "moc2": "moc2", "3": "moc3", "moc3": "moc3"}
    if value not in aliases:
        raise ValueError("Live2D 版本仅支持 moc2 或 moc3。")
    return aliases[value]


def _safe_model_dir(model_id: str, version: str = "moc2") -> Path | None:
    """安全解析指定版本的独立模型目录。"""
    if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", model_id):
        return None
    try:
        version = _normalize_live2d_version(version)
    except ValueError:
        return None
    root = (MODEL_ROOT / version).resolve()
    directory = (root / model_id).resolve()
    try:
        directory.relative_to(root)
    except ValueError:
        return None
    if not directory.is_dir():
        return None
    names = ("model.json", "model.default.json", f"{model_id}.model.json") if version == "moc2" else (f"{model_id}.model3.json", "model3.json")
    return directory if any((directory / name).is_file() for name in names) else None


def _model_asset_prefix(directory: Path, version: str = "moc2") -> str:
    """返回指定版本模型资源的站点绝对 URL 前缀。"""
    version = _normalize_live2d_version(version)
    relative = directory.resolve().relative_to((MODEL_ROOT / version).resolve())
    return f"/models/{version}/" + relative.as_posix().rstrip("/") + "/"


def _prefix_model_asset_paths(data: dict[str, Any], prefix: str) -> dict[str, Any]:
    """为新目录布局中的 MOC2 资源补上稳定 URL，避免相对路径解析发生变化。"""
    if not prefix:
        return data

    # 当前 Cubism 2 运行库会把 modelHomeDir 与模型、物理、姿势和动作文件路径直接拼接。
    # 配置入口仍通过 /models/<model_id>/model.json 提供，因此资源路径必须相对该入口
    # 回退到真实目录；不能写成 /models/... 绝对路径，否则 .moc 等文件会被重复拼接。
    relative_prefix = "../" + prefix.removeprefix("/models/")

    def prefixed(value: Any) -> Any:
        if not isinstance(value, str) or not value:
            return value
        if value.startswith(("/", "http://", "https://", "data:")):
            return value
        return relative_prefix + value.lstrip("./")

    for key in ("model", "physics", "pose"):
        if isinstance(data.get(key), str):
            data[key] = prefixed(data[key])
    if isinstance(data.get("textures"), list):
        data["textures"] = [prefixed(item) for item in data["textures"]]
    if isinstance(data.get("expressions"), list):
        for item in data["expressions"]:
            if isinstance(item, dict) and isinstance(item.get("file"), str):
                item["file"] = prefixed(item["file"])
    motions = data.get("motions")
    if isinstance(motions, dict):
        for group in motions.values():
            if isinstance(group, list):
                for item in group:
                    if isinstance(item, dict):
                        for asset_key in ("file", "sound"):
                            if isinstance(item.get(asset_key), str):
                                item[asset_key] = prefixed(item[asset_key])
    return data


def _model_config_path(directory: Path, model_id: str, version: str = "moc2", profile: str | None = None) -> Path | None:
    """查找指定版本和服装配置；profile 仅允许当前模型目录内的 model*.json 文件名。"""
    version = _normalize_live2d_version(version)
    if version == "moc2" and profile:
        # 兼容标准 model*.json 配置，以及 "<model_id>.model.json" 命名配置。
        valid_profile = (
            re.fullmatch(r"model(?:\.[A-Za-z0-9_-]+)*\.json", profile)
            or profile == f"{model_id}.model.json"
        )
        if not valid_profile:
            return None
        candidate = directory / profile
        try:
            candidate.resolve().relative_to(directory.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None
    names = ("model.json", "model.default.json", f"{model_id}.model.json") if version == "moc2" else (f"{model_id}.model3.json", "model3.json")
    for filename in names:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def _model_profiles(model_id: str, version: str = "moc2") -> list[dict[str, str]]:
    """列出角色模型的可选配置；MOC3 暂按单配置模型处理。"""
    version = _normalize_live2d_version(version)
    directory = _safe_model_dir(model_id, version)
    if directory is None:
        return []
    if version == "moc3":
        path = _model_config_path(directory, model_id, version)
        return [{"id": path.name, "name": "默认配置"}] if path else []
    candidates = []
    candidate_paths = list(directory.glob("model*.json"))
    # _model_config_path also supports "<model_id>.model.json" as the
    # canonical MOC2 config, so include it in the profile list as well.
    named_config = directory / f"{model_id}.model.json"
    if named_config.is_file() and named_config not in candidate_paths:
        candidate_paths.append(named_config)
    for path in candidate_paths:
        if path.name != f"{model_id}.model.json" and not re.fullmatch(r"model(?:\.[A-Za-z0-9_-]+)*\.json", path.name):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get("model"), str):
            continue
        textures = data.get("textures")
        if not isinstance(textures, list) or not textures or not all(isinstance(item, str) and item for item in textures):
            continue
        label = str(data.get("name") or ("默认服装" if path.name in {"model.json", "model.default.json"} else path.stem.removeprefix("model.")))
        candidates.append({"id": path.name, "name": label})
    candidates.sort(key=lambda item: (0 if item["id"] in {"model.json", "model.default.json"} else 1, item["id"].casefold()))
    return candidates


def _read_model_config(model_id: str, version: str = "moc2", profile: str | None = None) -> tuple[Path, dict[str, Any]]:
    version = _normalize_live2d_version(version)
    directory = _safe_model_dir(model_id, version)
    if directory is None:
        raise FileNotFoundError(f"{version} 模型目录不存在：{model_id}")
    config_path = _model_config_path(directory, model_id, version, profile)
    if config_path is None:
        raise FileNotFoundError(f"{version} 模型配置不存在或配置版本无效：models/{version}/{model_id}/{profile or ''}")
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{config_path.name} 必须是 JSON 对象")
    return directory, data


def _live2d_runtime_version() -> str:
    try:
        return _normalize_live2d_version(CONFIG.get("live2d", {}).get("runtime_version", "moc2"))
    except ValueError:
        logger.warning("Live2D runtime_version 配置无效，回退到 MOC2。")
        return "moc2"


def _default_model_id(version: str = "moc2") -> str:
    version = _normalize_live2d_version(version)
    live2d = CONFIG.get("live2d", {})
    configured = live2d.get("default_model_moc2", live2d.get("default_model", DEFAULT_MODEL)) if version == "moc2" else live2d.get("default_model_moc3", "")
    if isinstance(configured, str) and configured and _safe_model_dir(configured, version):
        return configured
    if version == "moc3":
        available = _available_models("moc3")
        return available[0]["id"] if available else ""
    return DEFAULT_MODEL


def _load_user_settings() -> dict[str, Any]:
    """从当前模块配置读取 Live2D、直播间和语音设置。"""
    manager = ConfigManager()
    live = manager.load("live", {})
    live2d = manager.load("live2d", {})
    danmaku = manager.load("danmaku", {"idle_tips": {}})
    voice = manager.load("voice", {})
    legacy_idle_tips = live2d.get("idle_tips")
    idle_tips = danmaku.get("idle_tips") if isinstance(danmaku.get("idle_tips"), dict) else {}
    # 首次升级时将旧 live2d.json 中的空闲提示迁移到 danmaku.json，保留用户原有设置。
    if not idle_tips and isinstance(legacy_idle_tips, dict) and legacy_idle_tips:
        idle_tips = legacy_idle_tips
        danmaku["idle_tips"] = idle_tips
        manager.save("danmaku", danmaku)
    if "idle_tips" in live2d:
        live2d.pop("idle_tips", None)
        manager.save("live2d", live2d)
    model_by_version = live2d.get("model_by_version")
    if not isinstance(model_by_version, dict):
        model_by_version = {}
    legacy_model = live2d.get("model")
    if "moc2" not in model_by_version and isinstance(legacy_model, str):
        model_by_version["moc2"] = legacy_model
        live2d["model_by_version"] = model_by_version
        manager.save("live2d", live2d)
    models = live2d.get("models") if isinstance(live2d.get("models"), dict) else {}
    tts = voice.get("tts") if isinstance(voice.get("tts"), dict) else {}
    try:
        room_id = max(0, int(live.get("room_id", CONFIG.get("bilibili", {}).get("room_id", 0))))
    except (TypeError, ValueError):
        room_id = 0
    return {
        "model": model_by_version.get("moc2") or str(live2d.get("default_model") or DEFAULT_MODEL),
        "model_by_version": model_by_version,
        "models": models,
        "runtime_version": _live2d_runtime_version(),
        "bilibili": {"room_id": room_id}, "tts": tts, "idle_tips": idle_tips,
    }


def _model_preferences(settings: dict[str, Any], model_id: str, model_version: str = "moc2") -> dict[str, Any]:
    models = settings.get("models", {})
    if not isinstance(models, dict):
        models = {}
    # 优先读取带版本前缀的新键；旧版纯 model_id 设置作为迁移回退。
    raw = models.get(f"{model_version}:{model_id}", models.get(model_id, {}) if model_version == "moc2" else {})
    if not isinstance(raw, dict):
        raw = {}

    def number(key: str, default: float, low: float, high: float) -> float:
        try:
            value = float(raw.get(key, default))
            if not math.isfinite(value):
                return default
            return max(low, min(high, value))
        except (TypeError, ValueError):
            return default

    def integer(key: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(raw.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "scale": number("scale", 1.0, 0.01, 1.5),
        "x": number("x", 0.0, -1.0, 1.0),
        "y": number("y", 0.0, -1.0, 1.0),
        "crop_top": number("crop_top", 0.0, 0.0, 45.0),
        "crop_right": number("crop_right", 0.0, 0.0, 45.0),
        "crop_bottom": number("crop_bottom", 0.0, 0.0, 45.0),
        "crop_left": number("crop_left", 0.0, 0.0, 45.0),
        "bubble_x": number("bubble_x", 50.0, 0.0, 100.0),
        "bubble_bottom": number("bubble_bottom", 45.0, 0.0, 95.0),
        "bubble_width": integer("bubble_width", 420, 240, 760),
        "bubble_max_height": integer("bubble_max_height", 320, 100, 700),
        "bubble_max_items": integer("bubble_max_items", 5, 1, 12),
        "bubble_duration": integer("bubble_duration", 10, 2, 60),
        "bubble_font_size": integer("bubble_font_size", 20, 12, 40),
        "profile": raw.get("profile") if isinstance(raw.get("profile"), str) else "",
        "motion_shortcuts_by_profile": raw.get("motion_shortcuts_by_profile") if isinstance(raw.get("motion_shortcuts_by_profile"), dict) else {},
    }


def _save_user_settings(settings: dict[str, Any]) -> None:
    """把用户设置分别保存到 live/live2d/danmaku/voice 模块配置。"""
    manager = ConfigManager()
    live = manager.load("live", {})
    live2d = manager.load("live2d", {})
    danmaku = manager.load("danmaku", {"idle_tips": {}})
    voice = manager.load("voice", {})

    bilibili = settings.get("bilibili") if isinstance(settings.get("bilibili"), dict) else {}
    live["room_id"] = max(0, int(bilibili.get("room_id", live.get("room_id", 0)) or 0))

    model_id = settings.get("model")
    if isinstance(model_id, str) and _safe_model_dir(model_id, "moc2"):
        live2d["model"] = model_id
    model_by_version = settings.get("model_by_version")
    if isinstance(model_by_version, dict):
        live2d["model_by_version"] = {
            version: value for version, value in model_by_version.items()
            if version in {"moc2", "moc3"} and isinstance(value, str)
            and _safe_model_dir(value, version)
        }
    models = settings.get("models")
    if isinstance(models, dict):
        live2d["models"] = models
    idle_tips = settings.get("idle_tips")
    if isinstance(idle_tips, dict):
        danmaku["idle_tips"] = idle_tips
    live2d.pop("idle_tips", None)
    tts = settings.get("tts")
    if isinstance(tts, dict):
        voice["tts"] = tts

    manager.save("live", live)
    manager.save("live2d", live2d)
    manager.save("danmaku", danmaku)
    manager.save("voice", voice)




def _tts_preferences(settings: dict[str, Any]) -> dict[str, Any]:
    raw = settings.get("tts")
    if not isinstance(raw, dict):
        raw = {}
    configured = CONFIG.get("tts", {}) if isinstance(CONFIG.get("tts"), dict) else {}

    def bounded_int(value: Any, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(value)))
        except (TypeError, ValueError):
            return default

    try:
        gift_min_amount = float(raw.get("gift_min_amount", 0))
        if not math.isfinite(gift_min_amount):
            gift_min_amount = 0.0
    except (TypeError, ValueError):
        gift_min_amount = 0.0
    gift_min_amount = max(0.0, min(100000.0, gift_min_amount))
    voice = str(raw.get("voice") or configured.get("voice") or "zh-CN-XiaoxiaoNeural")
    if voice not in TTS_VOICES:
        voice = "zh-CN-XiaoxiaoNeural"
    return {
        "danmaku_enabled": bool(raw.get("danmaku_enabled", True)),
        "gift_enabled": bool(raw.get("gift_enabled", True)),
        "super_chat_enabled": bool(raw.get("super_chat_enabled", True)),
        "voice": voice,
        "rate": bounded_int(raw.get("rate", 0), 0, -50, 100),
        "volume": bounded_int(raw.get("volume", 0), 0, -50, 50),
        "max_queue_length": bounded_int(raw.get("max_queue_length", 30), 30, 1, 200),
        "gift_min_amount": round(gift_min_amount, 2),
    }


def _save_tts_preferences(payload: dict[str, Any]) -> dict[str, Any]:
    with SETTINGS_LOCK:
        settings = _load_user_settings()
        candidate = dict(settings.get("tts") or {})
        candidate.update(payload)
        settings["tts"] = candidate
        preferences = _tts_preferences(settings)
        settings["tts"] = preferences
        _save_user_settings(settings)
    return preferences


IDLE_TIP_DEFAULT_MESSAGES = [
    "感谢你这么优秀还关注我！",
    "欢迎来到东郭老贼的直播间！",
    "感谢你的观看，感谢你的陪伴！",
]
IDLE_TIP_DEFAULT_TIME_MESSAGES = {
    "early_morning": ["早安！美好的一天就要开始了。"],
    "morning": ["上午好！保持专注，也别忘了适当放松哦。"],
    "lunch": ["午餐时间到！记得好好吃饭，给身体充充电吧！"],
    "after_lunch": ["午后容易犯困，不妨伸个懒腰，提提神？"],
    "afternoon": ["下午好！不要久坐，记得起来活动一下哦！"],
    "evening": ["傍晚了，记得放松一下，看看窗外的风景呀！"],
    "night": ["晚上好，今天过得怎么样？"],
    "late_night": ["深夜了，注意休息，别太累着自己哦！"],
    "midnight": ["哇，你是夜猫子呀？这么晚还不睡觉吗？"],
}


def _idle_tip_preferences(settings: dict[str, Any]) -> dict[str, Any]:
    raw = settings.get("idle_tips")
    if not isinstance(raw, dict):
        raw = {}

    def clean_messages(value: Any, fallback: list[str]) -> list[str]:
        if not isinstance(value, list):
            value = fallback
        result = []
        for item in value[:100]:
            if isinstance(item, str):
                message = item.strip()
                if message and len(message) <= 200:
                    result.append(message)
        return result

    try:
        interval = max(10, min(3600, int(raw.get("interval_seconds", 60))))
    except (TypeError, ValueError):
        interval = 60

    time_raw = raw.get("time_messages")
    if not isinstance(time_raw, dict):
        time_raw = {}
    time_messages = {
        key: clean_messages(time_raw.get(key), defaults)
        for key, defaults in IDLE_TIP_DEFAULT_TIME_MESSAGES.items()
    }
    return {
        "enabled": bool(raw.get("enabled", True)),
        "interval_seconds": interval,
        "messages": clean_messages(raw.get("messages"), IDLE_TIP_DEFAULT_MESSAGES),
        "time_messages": time_messages,
    }


@app.get("/api/danmaku/idle-tips/settings")
def get_idle_tip_settings():
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    return jsonify({"ok": True, "preferences": _idle_tip_preferences(settings)})


@app.post("/api/danmaku/idle-tips/settings")
def save_idle_tip_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400

    time_messages = payload.get("time_messages", {})
    if not isinstance(time_messages, dict):
        return jsonify({"ok": False, "error": "分时段提示语格式无效。"}), 400

    candidate = {
        "enabled": bool(payload.get("enabled", True)),
        "interval_seconds": payload.get("interval_seconds", 60),
        "messages": payload.get("messages", []),
        "time_messages": time_messages,
    }
    try:
        interval = int(candidate["interval_seconds"])
        if interval < 10 or interval > 3600:
            raise ValueError
        candidate["interval_seconds"] = interval
        if not isinstance(candidate["messages"], list) or len(candidate["messages"]) > 100:
            raise ValueError
        for message in candidate["messages"]:
            if not isinstance(message, str) or len(message.strip()) > 200:
                raise ValueError
        for key in IDLE_TIP_DEFAULT_TIME_MESSAGES:
            values = time_messages.get(key, [])
            if not isinstance(values, list) or len(values) > 100:
                raise ValueError
            if any(not isinstance(value, str) or len(value.strip()) > 200 for value in values):
                raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "提示间隔需为 10–3600 秒；每条提示语不能超过 200 个字符。"}), 400

    with SETTINGS_LOCK:
        settings = _load_user_settings()
        settings["idle_tips"] = candidate
        preferences = _idle_tip_preferences(settings)
        settings["idle_tips"] = preferences
        _save_user_settings(settings)
    return jsonify({"ok": True, "preferences": preferences})


def _cleanup_tts_cache() -> None:
    TTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 3600
    for path in TTS_CACHE_DIR.glob("*.mp3"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/api/voice/tts/settings")
def get_tts_settings():
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    return jsonify({"ok": True, "preferences": _tts_preferences(settings), "voices": TTS_VOICES})


@app.post("/api/voice/tts/settings")
def save_tts_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        preferences = _save_tts_preferences(payload)
    except Exception as exc:
        logger.exception("保存 TTS 设置失败")
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "preferences": preferences})


@app.post("/api/voice/tts/synthesize")
def synthesize_tts():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "语音内容不能为空。"}), 400
    if len(text) > 1000:
        return jsonify({"ok": False, "error": "单条语音内容不能超过 1000 个字符。"}), 400
    with SETTINGS_LOCK:
        preferences = _tts_preferences(_load_user_settings())
    try:
        import edge_tts

        _cleanup_tts_cache()
        filename = uuid.uuid4().hex + ".mp3"
        target = TTS_CACHE_DIR / filename

        async def generate() -> None:
            communicate = edge_tts.Communicate(
                text,
                preferences["voice"],
                rate=f'{preferences["rate"]:+d}%',
                volume=f'{preferences["volume"]:+d}%',
            )
            await communicate.save(str(target))

        asyncio.run(generate())
        if not target.is_file() or target.stat().st_size <= 0:
            raise RuntimeError("Edge TTS 未生成有效音频文件。")
        return jsonify({"ok": True, "url": f"/api/voice/tts/audio/{filename}"})
    except Exception as exc:
        logger.exception("Edge TTS 语音生成失败")
        return jsonify({"ok": False, "error": f"语音生成失败：{exc}"}), 502


@app.get("/api/voice/tts/audio/<filename>")
def get_tts_audio(filename: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.mp3", filename):
        abort(404)
    if not TTS_CACHE_DIR.is_dir():
        abort(404)
    return send_from_directory(TTS_CACHE_DIR, filename, mimetype="audio/mpeg", max_age=0)


@app.post("/api/voice/tts/test")
def enqueue_tts_test():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "试听文本不能为空。"}), 400
    if len(text) > 1000:
        return jsonify({"ok": False, "error": "试听文本不能超过 1000 个字符。"}), 400
    with VOICE_PLAYBACK_STATE_LOCK:
        if len(VOICE_TEST_QUEUE) >= VOICE_TEST_QUEUE.maxlen:
            return jsonify({"ok": False, "error": "试听队列已满，请稍后再试。"}), 429
        VOICE_TEST_QUEUE.append({"text": text})
    return jsonify({"ok": True, "message": "试听已加入 OBS 语音队列。"})


@app.get("/api/voice/tts/test-queue")
def get_tts_test_queue():
    with VOICE_PLAYBACK_STATE_LOCK:
        items = list(VOICE_TEST_QUEUE)
        VOICE_TEST_QUEUE.clear()
    return jsonify({"ok": True, "items": items})


@app.get("/api/voice/tts/state")
def get_tts_playback_state():
    with VOICE_PLAYBACK_STATE_LOCK:
        state = dict(VOICE_PLAYBACK_STATE)
        state["queue"] = list(VOICE_PLAYBACK_STATE.get("queue", []))
    if time.monotonic() - float(state.get("updated_at", 0.0) or 0.0) > 2.0:
        state["current"] = ""
        state["queue"] = []
        state["speaking"] = False
        state["level"] = 0.0
    state.pop("updated_at", None)
    return jsonify({"ok": True, **state})


@app.post("/api/voice/tts/state")
def update_tts_playback_state():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    current = str(payload.get("current") or "")[:1000]
    raw_queue = payload.get("queue", [])
    if not isinstance(raw_queue, list):
        return jsonify({"ok": False, "error": "语音等待队列格式无效。"}), 400
    queue = [str(value)[:1000] for value in raw_queue[:200]]
    level = None
    if "level" in payload:
        try:
            level = float(payload.get("level", 0.0))
            if not math.isfinite(level):
                level = 0.0
        except (TypeError, ValueError):
            level = 0.0
    with VOICE_PLAYBACK_STATE_LOCK:
        VOICE_PLAYBACK_STATE.update({
            "current": current,
            "queue": queue,
            "speaking": bool(payload.get("speaking", False)),
            "updated_at": time.monotonic(),
        })
        if level is not None:
            VOICE_PLAYBACK_STATE["level"] = max(0.0, min(1.0, level))
    return jsonify({"ok": True})


def _obs_ws_request(ws: Any, request_type: str, request_data: dict[str, Any] | None = None) -> dict[str, Any]:
    request_id = uuid.uuid4().hex
    ws.send(json.dumps({
        "op": 6,
        "d": {
            "requestType": request_type,
            "requestId": request_id,
            "requestData": request_data or {},
        },
    }))
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        message = json.loads(ws.recv())
        if message.get("op") != 7:
            continue
        data = message.get("d", {})
        if data.get("requestId") != request_id:
            continue
        status = data.get("requestStatus", {})
        if not status.get("result", False):
            raise RuntimeError(status.get("comment") or f"OBS 请求失败：{request_type}")
        return data.get("responseData") or {}
    raise TimeoutError(f"等待 OBS 请求响应超时：{request_type}")


def _read_obs_password() -> str:
    """只从统一凭据库读取 OBS 密码，不再读取旧明文密码文件。"""
    try:
        secret = CredentialStore().load("obs") or {}
        return str(secret.get("password", "") or "")
    except Exception as exc:
        logger.warning("读取 OBS 凭据失败：%s", type(exc).__name__)
        return ""


def _open_obs_websocket() -> Any:
    """按统一 OBS 配置和凭据完成 WebSocket v5 握手并返回已认证连接。"""
    obs_config = CONFIG.get("obs_websocket", {})
    if not isinstance(obs_config, dict):
        obs_config = {}
    host = str(obs_config.get("host", "127.0.0.1")).strip() or "127.0.0.1"
    try:
        port = int(obs_config.get("port", 4455))
        timeout = max(0.5, min(5.0, float(obs_config.get("timeout", 2.5))))
    except (TypeError, ValueError):
        port, timeout = 4455, 2.5

    ws = websocket.create_connection(
        f"ws://{host}:{port}",
        timeout=timeout,
        suppress_origin=True,
    )
    try:
        hello = json.loads(ws.recv())
        if hello.get("op") != 0:
            raise RuntimeError("OBS WebSocket 未返回 Hello 握手。")
        hello_data = hello.get("d", {})
        identify: dict[str, Any] = {"rpcVersion": 1, "eventSubscriptions": 0}
        authentication = hello_data.get("authentication")
        if authentication:
            password = _read_obs_password()
            if not password:
                raise RuntimeError("OBS WebSocket 已启用密码验证，但未配置有效密码。")
            secret = base64.b64encode(
                hashlib.sha256((password + authentication["salt"]).encode("utf-8")).digest()
            ).decode("ascii")
            identify["authentication"] = base64.b64encode(
                hashlib.sha256((secret + authentication["challenge"]).encode("utf-8")).digest()
            ).decode("ascii")
        ws.send(json.dumps({"op": 1, "d": identify}))
        identified = json.loads(ws.recv())
        if identified.get("op") != 2:
            raise RuntimeError("OBS WebSocket 身份验证未成功。")
        return ws
    except Exception:
        try:
            ws.close()
        except Exception:
            pass
        raise


def _refresh_obs_browser_sources() -> dict[str, Any]:
    """尝试通过 OBS WebSocket 刷新指向 /live2d/display 的浏览器源；失败时不影响设置保存。"""
    ws = None
    try:
        ws = _open_obs_websocket()
        response = _obs_ws_request(ws, "GetInputList")
        refreshed = 0
        for item in response.get("inputs", []):
            if item.get("inputKind") != "browser_source":
                continue
            input_name = item.get("inputName")
            if not isinstance(input_name, str):
                continue
            source_info = _obs_ws_request(ws, "GetInputSettings", {"inputName": input_name})
            settings = source_info.get("inputSettings") or {}
            source_url = settings.get("url", "")
            if not isinstance(source_url, str):
                continue
            if not _is_display_browser_url(source_url):
                continue
            _obs_ws_request(ws, "PressInputPropertiesButton", {
                "inputName": input_name,
                "propertyName": "refreshnocache",
            })
            refreshed += 1
        if refreshed:
            message = f"已通过 OBS WebSocket 刷新 {refreshed} 个展示浏览器源。"
        else:
            message = "已连接 OBS，但未找到 URL 指向 /live2d/display 的浏览器源，已跳过刷新。"
        logger.info("%s", message)
        return {"attempted": True, "refreshed": refreshed, "message": message}
    except Exception as exc:
        message = f"OBS WebSocket 不可用，已跳过浏览器源刷新：{exc}"
        logger.info("%s", message)
        return {"attempted": True, "refreshed": 0, "message": message}
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass



# OBS WebSocket 连接后只刷新综合展示源，不改变任何场景项的可见状态。
OBS_DISPLAY_LOCK = threading.RLock()
OBS_COMBINED_REFRESH_STOP = threading.Event()
OBS_COMBINED_DISPLAY_REFRESHED = False
CREDENTIAL_WATCHDOG_STOP = threading.Event()
CREDENTIAL_WATCHDOG_THREAD: threading.Thread | None = None
OBS_COMBINED_REFRESH_THREAD: threading.Thread | None = None
APPLICATION_SHUTDOWN_STARTED = threading.Event()
APPLICATION_SHUTDOWN_LOCK = threading.RLock()
APPLICATION_SHUTDOWN_FINISHED = threading.Event()


def _display_service_address() -> tuple[str, int]:
    """返回本地展示服务绑定地址；通配绑定地址允许匹配 OBS 使用的实际访问主机。"""
    app_config = CONFIG.get("app", {})
    if not isinstance(app_config, dict):
        app_config = {}
    host = str(app_config.get("host", "127.0.0.1") or "127.0.0.1").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    # 0.0.0.0 / :: 表示监听所有网卡，不能拿它与浏览器源 URL 的主机名直接比较。
    if host in {"", "0.0.0.0", "::"}:
        host = ""
    try:
        port = int(app_config.get("port", 8768))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        port = 8768
    return host, port


def _is_combined_display_browser_url(value: Any) -> bool:
    """匹配当前程序地址下的默认综合展示页或指定布局展示页。"""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = urlparse(value.strip())
        expected_host, expected_port = _display_service_address()
        actual_host = (parsed.hostname or "").lower()
        actual_port = parsed.port
        if actual_port is None:
            actual_port = 443 if parsed.scheme.lower() == "https" else 80
        loopbacks = {"localhost", "127.0.0.1", "::1"}
        same_host = not expected_host or actual_host == expected_host or (
            actual_host in loopbacks and expected_host in loopbacks
        )
        path = parsed.path.rstrip("/")
        is_combined_path = path == "/display/combined" or (
            path.startswith("/display/combined/") and bool(path.removeprefix("/display/combined/"))
        )
        return (
            parsed.scheme.lower() in {"http", "https"}
            and same_host
            and actual_port == expected_port
            and is_combined_path
        )
    except (TypeError, ValueError):
        return False


def _voice_engine_browser_url() -> str:
    """返回 OBS 全局语音引擎使用的本机服务地址。"""
    app_config = CONFIG.get("app", {})
    if not isinstance(app_config, dict):
        app_config = {}
    host = str(app_config.get("host", "127.0.0.1") or "127.0.0.1").strip()
    if host in {"", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    _, port = _display_service_address()
    return f"http://{host}:{port}/voice/display"


def _ensure_obs_voice_engine_source(ws: Any) -> dict[str, Any]:
    """创建一个共享的隐藏语音浏览器源，并将同一输入加入当前场景集合的全部场景。"""
    scene_data = _obs_ws_request(ws, "GetSceneList")
    scenes = [
        str(item.get("sceneName") or "").strip()
        for item in scene_data.get("scenes", [])
        if isinstance(item, dict) and str(item.get("sceneName") or "").strip()
    ]
    if not scenes:
        raise RuntimeError("OBS 当前场景集合中没有可用场景。")

    expected_url = _voice_engine_browser_url()
    expected_path = "/voice/display"
    base_name = "B站直播弹幕助手-全局语音引擎"
    inputs = _obs_ws_request(ws, "GetInputList").get("inputs", [])
    source_name = ""
    source_exists = False

    # 先按专用来源名称查找，应用端口或绑定地址变更后也能复用旧输入。
    for item in inputs:
        if not isinstance(item, dict) or item.get("inputKind") != "browser_source":
            continue
        name = str(item.get("inputName") or "").strip()
        if name == base_name or name.startswith(base_name + " "):
            source_name = name
            source_exists = True
            break

    # 兼容用户曾手动添加过 /voice/display 的情况，优先复用本机地址上的浏览器源。
    if not source_name:
        expected = urlparse(expected_url)
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        for item in inputs:
            if not isinstance(item, dict) or item.get("inputKind") != "browser_source":
                continue
            name = str(item.get("inputName") or "").strip()
            if not name:
                continue
            settings_data = _obs_ws_request(ws, "GetInputSettings", {"inputName": name})
            url = str((settings_data.get("inputSettings") or {}).get("url") or "")
            try:
                parsed = urlparse(url)
                if (
                    parsed.path.rstrip("/") == expected_path
                    and (
                        parsed.hostname in local_hosts
                        or parsed.hostname == expected.hostname
                    )
                ):
                    source_name = name
                    source_exists = True
                    break
            except ValueError:
                continue

    if not source_name:
        used_names = {
            str(item.get("inputName") or "")
            for item in inputs if isinstance(item, dict)
        }
        source_name = base_name
        suffix = 2
        while source_name in used_names:
            source_name = f"{base_name} {suffix}"
            suffix += 1

    css = (
        "html,body{margin:0!important;width:2px!important;height:2px!important;"
        "min-width:2px!important;min-height:2px!important;overflow:hidden!important;"
        "background:rgba(0,0,0,0)!important;opacity:0!important;}"
    )
    input_settings = {
        "url": expected_url,
        "width": 2,
        "height": 2,
        "css": css,
        "shutdown": False,
        "restart_when_active": False,
        # 将浏览器音频作为独立 OBS 音频源输出，不能依赖桌面音频回环。
        "reroute_audio": True,
    }

    if source_exists:
        _obs_ws_request(ws, "SetInputSettings", {
            "inputName": source_name,
            "inputSettings": input_settings,
            "overlay": True,
        })
    else:
        _obs_ws_request(ws, "CreateInput", {
            "sceneName": scenes[0],
            "inputName": source_name,
            "inputKind": "browser_source",
            "inputSettings": input_settings,
            "sceneItemEnabled": True,
        })

    added = 0
    for scene_name in scenes:
        scene_items = _obs_ws_request(ws, "GetSceneItemList", {"sceneName": scene_name}).get("sceneItems", [])
        existing = next((
            item for item in scene_items
            if isinstance(item, dict) and item.get("sourceName") == source_name
        ), None)
        if existing is None:
            _obs_ws_request(ws, "CreateSceneItem", {
                "sceneName": scene_name,
                "sourceName": source_name,
                "sceneItemEnabled": True,
            })
            added += 1
        elif not bool(existing.get("sceneItemEnabled", True)):
            _obs_ws_request(ws, "SetSceneItemEnabled", {
                "sceneName": scene_name,
                "sceneItemId": int(existing["sceneItemId"]),
                "sceneItemEnabled": True,
            })

    logger.info(
        "OBS 全局语音引擎已就绪：共享浏览器源“%s”，已加入 %s 个场景（本次新增 %s 个场景项）。",
        source_name, len(scenes), added,
    )
    return {"ready": True, "source_name": source_name, "scene_count": len(scenes), "added": added}


def _refresh_obs_combined_display_sources(ensure_voice_engine: bool = True) -> dict[str, Any]:
    """刷新综合展示浏览器源，并确保全局语音引擎在当前场景集合中持续启用。"""
    with OBS_DISPLAY_LOCK:
        ws = None
        try:
            ws = _open_obs_websocket()
            inputs = _obs_ws_request(ws, "GetInputList").get("inputs", [])
            refreshed = 0
            for item in inputs:
                if item.get("inputKind") != "browser_source":
                    continue
                input_name = item.get("inputName")
                if not isinstance(input_name, str) or not input_name:
                    continue
                source_info = _obs_ws_request(
                    ws, "GetInputSettings", {"inputName": input_name}
                )
                settings = source_info.get("inputSettings") or {}
                if not _is_combined_display_browser_url(settings.get("url")):
                    continue
                _obs_ws_request(ws, "PressInputPropertiesButton", {
                    "inputName": input_name,
                    "propertyName": "refreshnocache",
                })
                refreshed += 1

            voice_engine = None
            if ensure_voice_engine:
                voice_engine = _ensure_obs_voice_engine_source(ws)
                # OBS WebSocket 创建/配置 Browser Source 成功，不代表其 Chromium 页面已实际启动。
                # 尤其复用已有来源且 URL 未变化时，SetInputSettings 不保证触发页面重新加载。
                # 在启动阶段场景项全部启用后主动刷新一次，确保 /voice/display 真正发起加载。
                if voice_engine and voice_engine.get("ready"):
                    source_name = str(voice_engine.get("source_name") or "").strip()
                    if source_name:
                        _obs_ws_request(ws, "PressInputPropertiesButton", {
                            "inputName": source_name,
                            "propertyName": "refreshnocache",
                        })
                        voice_engine["refreshed"] = True
                        logger.info(
                            "OBS 全局语音引擎已触发启动页面刷新：共享浏览器源“%s”。",
                            source_name,
                        )

            if refreshed or (voice_engine and voice_engine.get("ready")):
                details = []
                if refreshed:
                    details.append(f"刷新 {refreshed} 个综合展示浏览器源")
                if voice_engine:
                    details.append(
                        f"全局语音引擎已加入 {voice_engine['scene_count']} 个场景"
                    )
                message = "OBS WebSocket 已连接，" + "；".join(details) + "。"
                logger.info("%s", message)
                return {
                    "ok": True,
                    "refreshed": refreshed,
                    "voice_engine": voice_engine,
                    "message": message,
                }
            message = "OBS WebSocket 已连接，但未找到综合展示浏览器源，且全局语音引擎尚未就绪；稍后继续检查。"
            logger.info("%s", message)
            return {"ok": False, "refreshed": 0, "voice_engine": voice_engine, "message": message}
        except Exception as exc:
            message = f"OBS WebSocket 不可用，已跳过本次综合展示源刷新：{exc}"
            logger.info("%s", message)
            return {"ok": False, "refreshed": 0, "message": message}
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass


def _start_obs_refresh_thread(name: str) -> None:
    """启动短时 OBS 刷新任务；与统一关闭入口串行化，避免最终刷新后又启动新任务。"""
    with APPLICATION_SHUTDOWN_LOCK:
        if APPLICATION_SHUTDOWN_STARTED.is_set():
            return
        worker = threading.Thread(
            target=_refresh_obs_combined_display_sources,
            name=name,
            daemon=True,
        )
        worker.start()


def _obs_combined_display_refresh_watchdog() -> None:
    """等待 OBS WebSocket 可用，刷新综合展示源并建立全局语音引擎；不依赖 B 站连接状态。"""
    global OBS_COMBINED_DISPLAY_REFRESHED
    while not OBS_COMBINED_REFRESH_STOP.wait(3.0):
        if OBS_COMBINED_DISPLAY_REFRESHED:
            break
        result = _refresh_obs_combined_display_sources()
        if result.get("ok") and (result.get("refreshed", 0) > 0 or (result.get("voice_engine") or {}).get("ready")):
            OBS_COMBINED_DISPLAY_REFRESHED = True
            break


def _shutdown_application_and_refresh_obs() -> None:
    """统一、幂等地停止事件来源、子进程、业务模块和 OBS 刷新。"""
    global OBS_COMBINED_DISPLAY_REFRESHED
    with APPLICATION_SHUTDOWN_LOCK:
        if APPLICATION_SHUTDOWN_FINISHED.is_set():
            return

        APPLICATION_SHUTDOWN_STARTED.set()
        shutdown_deadline = time.monotonic() + 65.0

        def remaining_budget(maximum: float) -> float:
            return max(0.0, min(maximum, shutdown_deadline - time.monotonic()))

        logger.warning("开始有序退出：封锁新事件 -> 停止后台来源 -> 关闭子进程和业务模块 -> 最终刷新 OBS。")
        OBS_COMBINED_REFRESH_STOP.set()
        CREDENTIAL_WATCHDOG_STOP.set()
        APPLICATION_RUNTIME.begin_shutdown()

        # 先断开事件来源。begin_shutdown 已经同步封锁连接回调对模块的影响。
        try:
            if not LIVE_CLIENT.stop(timeout=remaining_budget(6.0)):
                logger.error("B站连接线程在 6 秒内未退出；保留其 daemon 状态并继续清理其他资源。")
        except Exception:
            logger.exception("停止 B 站连接时发生异常，仍将继续执行其他退出清理。")

        # Hex 是独立子进程；只停止本主程序托管且身份核验通过的实例。
        try:
            LOL_HEX_EXTENSION.stop_if_owned(timeout=remaining_budget(6.0))
        except Exception:
            logger.exception("停止 LOL Hex 扩展时发生异常，仍将继续执行其他退出清理。")

        try:
            APPLICATION_RUNTIME.shutdown(timeout=max(0.1, remaining_budget(37.0)))
        except Exception:
            logger.exception("关闭业务模块时发生异常，仍将继续执行退出刷新。")

        # watchdog 已收到停止信号；有界等待它们退出，不等待已结束或尚未创建的线程。
        for worker in (CREDENTIAL_WATCHDOG_THREAD, OBS_COMBINED_REFRESH_THREAD):
            if worker is not None and worker is not threading.current_thread() and worker.is_alive():
                worker.join(timeout=remaining_budget(1.0))
                if worker.is_alive():
                    logger.warning("后台 watchdog 未在 1 秒内退出：%s", worker.name)

        # 所有模块 shutdown 完成后最后刷新综合展示源；独立 daemon 线程最多等待 5 秒，
        # 防止 OBS 无响应或此前刷新锁未释放时拖住主程序退出。
        try:
            refresh_timeout = remaining_budget(5.0)
            if refresh_timeout <= 0:
                logger.warning("退出总时间预算已耗尽，跳过 OBS 最终刷新。")
            else:
                refresh_result: dict[str, Any] = {}

                def refresh_final_obs() -> None:
                    try:
                        refresh_result["result"] = _refresh_obs_combined_display_sources(
                            ensure_voice_engine=False
                        )
                    except Exception as exc:
                        refresh_result["error"] = exc
                        logger.exception("程序退出阶段最终刷新 OBS 综合展示源失败。")

                final_refresh_thread = threading.Thread(
                    target=refresh_final_obs,
                    name="obs-final-shutdown-refresh",
                    daemon=True,
                )
                final_refresh_thread.start()
                final_refresh_thread.join(timeout=refresh_timeout)
                if final_refresh_thread.is_alive():
                    logger.warning("OBS 最终刷新超过剩余 %.1f 秒仍未完成；不再阻塞主程序退出。", refresh_timeout)
                elif "error" in refresh_result:
                    logger.warning("程序退出阶段最终 OBS 综合展示源刷新失败。")
                else:
                    result = refresh_result.get("result", {})
                    if result.get("ok") and result.get("refreshed", 0) > 0:
                        OBS_COMBINED_DISPLAY_REFRESHED = True
                        logger.info(
                            "程序退出阶段 OBS 综合展示源刷新成功：刷新 %s 个浏览器源。",
                            result["refreshed"],
                        )
                    else:
                        logger.warning(
                            "程序退出阶段 OBS 综合展示源刷新未成功，不再重试：%s",
                            result.get("message", "未知错误"),
                        )
        finally:
            APPLICATION_SHUTDOWN_FINISHED.set()


def _available_models(version: str = "moc2") -> list[dict[str, str]]:
    """仅扫描指定版本目录，并按该版本的配置格式验证模型入口。"""
    version = _normalize_live2d_version(version)
    result: list[dict[str, str]] = []
    version_root = MODEL_ROOT / version
    if not version_root.is_dir():
        return result
    for directory in sorted(version_root.iterdir(), key=lambda item: item.name.casefold()):
        if not directory.is_dir() or _safe_model_dir(directory.name, version) is None:
            continue
        config_path = _model_config_path(directory, directory.name, version)
        if config_path is None:
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                continue
            if version == "moc2":
                valid = isinstance(data.get("model"), str) and isinstance(data.get("textures"), list) and bool(data["textures"])
            else:
                refs = data.get("FileReferences")
                valid = isinstance(refs, dict) and isinstance(refs.get("Moc"), str) and isinstance(refs.get("Textures"), list) and bool(refs["Textures"])
            if valid:
                result.append({"id": directory.name, "name": str(data.get("name") or directory.name), "version": version})
        except Exception:
            logger.warning("跳过无效 %s 模型配置：%s", version.upper(), config_path)
    return result


def _model_assets(model_id: str, version: str = "moc2") -> tuple[bool, list[str], dict[str, Any]]:
    version = _normalize_live2d_version(version)
    directory = _safe_model_dir(model_id, version)
    if directory is None:
        return False, [f"{version} 模型目录不存在：{model_id}"], {}
    try:
        _, data = _read_model_config(model_id, version)
    except Exception as exc:
        return False, [str(exc)], {}
    if version == "moc3":
        refs = data.get("FileReferences", {})
        required = [refs.get("Moc")] + (refs.get("Textures") if isinstance(refs.get("Textures"), list) else []) if isinstance(refs, dict) else []
        missing = []
        for relative in required:
            if not isinstance(relative, str) or not relative:
                missing.append(str(relative))
                continue
            candidate = (directory / relative).resolve()
            try:
                candidate.relative_to(directory.resolve())
            except ValueError:
                missing.append(f"非法资源路径：{relative}")
                continue
            if not candidate.is_file():
                missing.append(relative)
        return not missing, missing, data

    references: list[str] = []
    for key in ("model", "physics", "pose"):
        value = data.get(key)
        if isinstance(value, str) and value:
            references.append(value)
    values = data.get("textures", [])
    if isinstance(values, list):
        references.extend(item for item in values if isinstance(item, str) and item)
    expressions = data.get("expressions", [])
    if isinstance(expressions, list):
        references.extend(item["file"] for item in expressions if isinstance(item, dict) and isinstance(item.get("file"), str))
    motions = data.get("motions", {})
    if isinstance(motions, dict):
        for group in motions.values():
            if isinstance(group, list):
                for item in group:
                    if not isinstance(item, dict):
                        continue
                    for asset_key in ("file", "sound"):
                        if isinstance(item.get(asset_key), str) and item[asset_key]:
                            references.append(item[asset_key])

    missing: list[str] = []
    for relative in references:
        candidate = (directory / relative).resolve()
        try:
            candidate.relative_to(directory.resolve())
        except ValueError:
            missing.append(f"非法资源路径：{relative}")
            continue
        if not candidate.is_file():
            missing.append(relative)
    return not missing, missing, data


def _live2d_enabled() -> bool:
    """读取 Live2D 运行时总开关；旧配置缺少该字段时默认保持启用。"""
    return bool(CONFIG.get("live2d", {}).get("enabled", True))


def _moc3_runtime_available() -> bool:
    """检查 Cubism 5 R5 Core、Framework bundle 与 WebGL shader 是否齐备。"""
    runtime_root = BASE_DIR / "static" / "js" / "live2d" / "moc3"
    core = runtime_root / "Core" / "live2dcubismcore.min.js"
    framework_bundle = runtime_root / "runtime.js"
    shader = runtime_root / "Framework" / "Shaders" / "WebGL" / "vertshadersrc.vert"
    return core.is_file() and framework_bundle.is_file() and shader.is_file()


def model_status() -> dict[str, Any]:
    enabled = _live2d_enabled()
    version = _live2d_runtime_version()
    settings = _load_user_settings()
    model_id = settings.get("model_by_version", {}).get(version) or _default_model_id(version)
    directory = _safe_model_dir(model_id, version) if model_id else None
    model_config = _model_config_path(directory, model_id, version) if directory else None
    runtime_file = BASE_DIR / "static" / "js" / "live2d.min.js" if version == "moc2" else BASE_DIR / "static" / "live2d" / "moc3" / "Core" / "live2dcubismcore.min.js"
    assets_ok, missing, _ = _model_assets(model_id, version) if enabled and model_id else (False, [], {})
    with RUNTIME_STATE_LOCK:
        runtime_state = dict(RUNTIME_STATE)
    if not enabled:
        runtime_state = {"state": "not_started", "message": "Live2D 运行时已停用。"}
    return {
        "enabled": enabled,
        "format": version.upper(),
        "version": version,
        "name": model_id,
        "directory_exists": bool(directory),
        "config_exists": bool(model_config and model_config.is_file()),
        "runtime_file_exists": runtime_file.is_file(),
        "runtime_supported": version == "moc2" or _moc3_runtime_available(),
        "assets_ready": assets_ok,
        "missing_assets": missing,
        "runtime_state": runtime_state["state"],
        "runtime_ready": enabled and (version == "moc2" or _moc3_runtime_available()) and runtime_state["state"] == "loaded",
        "message": (
            "已选择 MOC3，但 Cubism 5 R5 运行时文件缺失或不完整；不会回退到 MOC2。"
            if version == "moc3" and not _moc3_runtime_available()
            else runtime_state["message"]
        ),
    }


def _append_danmaku(item: dict[str, Any]) -> None:
    global DANMAKU_SEQUENCE
    with DANMAKU_LOCK:
        DANMAKU_SEQUENCE += 1
        record = dict(item)
        record["id"] = DANMAKU_SEQUENCE
        DANMAKU_ITEMS.append(record)


def _dispatch_live_event(item: dict[str, Any]) -> None:
    """只接收真实 WebSocket 认证连接期间的新事件，再投递给统一事件总线。"""
    if APPLICATION_SHUTDOWN_STARTED.is_set():
        return
    with BILIBILI_STATE_LOCK:
        if not BILIBILI_STATE["connected"]:
            return
    _append_danmaku(item)
    APPLICATION_RUNTIME.publish_event(item)


def _set_bilibili_connection_state(state: str, message: str) -> None:
    with BILIBILI_STATE_LOCK:
        previous_state = BILIBILI_STATE["connection_state"]
        BILIBILI_STATE["connection_state"] = state
        BILIBILI_STATE["connected"] = state == "connected"
        BILIBILI_STATE["message"] = message
    if previous_state == "connected" and state != "connected":
        # 清空断线前尚未被页面读取的缓存，防止重连后补发旧弹幕。
        with DANMAKU_LOCK:
            DANMAKU_ITEMS.clear()
        APPLICATION_RUNTIME.connection_lost()
    elif previous_state != "connected" and state == "connected":
        APPLICATION_RUNTIME.connection_restored()
    logger.info("B站直播连接状态：%s - %s", state, message)


def _login_status() -> tuple[bool, str, dict[str, str] | None]:
    valid, message, cookies = validate_cookie_file(_credential_path())
    should_stop = False
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["logged_in"] = valid
        if not valid:
            BILIBILI_STATE["message"] = message
        elif BILIBILI_STATE["connection_state"] == "disconnected":
            BILIBILI_STATE["message"] = message
        if not valid and BILIBILI_STATE["connection_state"] in {"connected", "connecting", "reconnecting", "stopping"}:
            should_stop = True
    if should_stop and not LIVE_CLIENT.stop():
        logger.warning("登录凭据失效后，B站连接线程仍在停止中。")
    return valid, message, cookies



def _music_cookie_provider() -> dict[str, str]:
    """只向已认证连接的歌曲模块提供当前连接凭据，不重复验证或改变主连接状态。"""
    with BILIBILI_STATE_LOCK:
        if not BILIBILI_STATE["connected"]:
            return {}
        cookies = dict(getattr(LIVE_CLIENT, "credential_data", {}) or {})
    return cookies


LIVE2D_MODULE = Live2DModule()
VOICE_MODULE = VoiceModule()
APPLICATION_RUNTIME.register_module(
    "live2d", LIVE2D_MODULE,
    initial_state="running" if _live2d_enabled() else "stopped",
    requires_connection=False,
)
APPLICATION_RUNTIME.register_module("voice", VOICE_MODULE, initial_state="stopped", auto_start_on_connect=True)


def _normalize_admin_uids(values) -> list[int]:
    result = []
    if isinstance(values, list):
        for value in values:
            raw = str(value).strip()
            if raw.isdigit() and int(raw) > 0 and int(raw) not in result:
                result.append(int(raw))
    return sorted(result)


def _load_system_admin_uids() -> list[int]:
    """系统管理员只从 config/app.json 的统一名单读取。"""
    app_config = ConfigManager().load("app", {})
    return _normalize_admin_uids(app_config.get("admin_uids", []))


def _module_config_with_system_admins(module_name: str) -> dict[str, Any]:
    config = ConfigManager().load(module_name, {})
    config["admin_uids"] = _load_system_admin_uids()
    return config


MUSIC_MODULE = MusicModuleFacade(
    event_bus=APPLICATION_RUNTIME.events,
    config=_module_config_with_system_admins("music"),
    cookie_provider=_music_cookie_provider,
    room_id=int(_load_user_settings()["bilibili"]["room_id"]),
)
APPLICATION_RUNTIME.register_module("music", MUSIC_MODULE)

VIDEO_MODULE = VideoModuleFacade(
    event_bus=APPLICATION_RUNTIME.events,
    config=_module_config_with_system_admins("video"),
    cookie_provider=_music_cookie_provider,
    room_id=int(_load_user_settings()["bilibili"]["room_id"]),
    music_module=MUSIC_MODULE.bot,
)
APPLICATION_RUNTIME.register_module("video", VIDEO_MODULE)




def _connect_saved_room(source: str) -> tuple[bool, str]:
    """使用已保存的有效 Cookie 连接已保存的直播间；绝不匿名连接。"""
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    room_id = int(settings["bilibili"]["room_id"])
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["room_id"] = room_id
        current_state = BILIBILI_STATE["connection_state"]

    if room_id <= 0:
        return False, "尚未设置直播间房间号，登录已保留，请先设置房间号。"
    if current_state in {"connected", "connecting", "reconnecting"}:
        return True, "直播间连接任务已经启动。"

    valid, message, cookies = _login_status()
    if not valid or not cookies:
        return False, message
    try:
        LIVE_CLIENT.start(room_id, cookies)
    except BilibiliClientError as exc:
        return False, str(exc)

    logger.info("已根据%s自动启动直播间连接：room_id=%s", source, room_id)
    return True, f"已启动直播间 {room_id} 连接，等待 B 站 WebSocket 认证。"


def _restore_saved_live_connection() -> None:
    """启动时使用已保存的有效登录凭据自动连接已保存的直播间。"""
    connected, message = _connect_saved_room("系统启动")
    with BILIBILI_STATE_LOCK:
        room_id = BILIBILI_STATE["room_id"]
        connection_state = BILIBILI_STATE["connection_state"]
        # 未发起连接时展示具体原因；连接线程启动后由状态回调负责更新提示。
        if not connected and connection_state == "disconnected":
            BILIBILI_STATE["message"] = message
    logger.info(
        "启动自动连接检查完成：已保存房间号=%s，连接任务已启动=%s，当前状态=%s。%s",
        room_id, connected, connection_state, message,
    )


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/live2d/display")
def display():
    # Live2D 完整展示：包含模型、弹幕和 TTS。
    return render_template("display.html", display_mode="all", live2d_enabled=_live2d_enabled(), live2d_version=_live2d_runtime_version())


@app.get("/live2d/moc3/runtime/<model_id>")
def live2d_moc3_runtime_page(model_id: str):
    if not _live2d_enabled() or not _moc3_runtime_available():
        abort(404)
    if _safe_model_dir(model_id, "moc3") is None:
        abort(404)
    assets_ok, missing, _ = _model_assets(model_id, "moc3")
    if not assets_ok:
        abort(404)
    return render_template(
        "live2d_moc3_runtime.html",
        model_url=f"/models/moc3/{model_id}/model.json",
    )



@app.get("/live2d/moc2/display")
def live2d_display_page():
    # 历史 MOC2 独立入口仍固定使用 MOC2；新入口统一使用 /live2d/display。
    return render_template("display.html", display_mode="live2d", live2d_enabled=_live2d_enabled(), live2d_version="moc2")

@app.get("/live2d/moc3/display")
def live2d_moc3_display_page():
    # MOC3 综合展示页：沿用完整展示模板，固定选择 MOC3 运行时。
    if not _moc3_runtime_available():
        abort(404)
    return render_template("display.html", display_mode="all", live2d_enabled=_live2d_enabled(), live2d_version="moc3")


@app.get("/danmaku/display")
def danmaku_display_page():
    return render_template("display.html", display_mode="danmaku", live2d_enabled=_live2d_enabled())


@app.get("/voice/display")
def tts_display_page():
    return render_template("display.html", display_mode="tts", live2d_enabled=_live2d_enabled())


@app.get("/live2d")
def live2d_settings_page():
    # 独立 Live2D 配置入口；展示模板内提供模型、动作、表情和画面参数设置。
    return render_template("display.html", display_mode="all", live2d_enabled=_live2d_enabled(), live2d_version=_live2d_runtime_version())


@app.get("/dashboard")
def dashboard_page():
    return render_template("combined_display.html", display_mode=False)


@app.get("/danmaku")
def danmaku_settings_page():
    return render_template("danmaku.html")


@app.get("/voice")
def voice_settings_page():
    return render_template("voice.html")


@app.get("/settings")
def system_settings_page():
    return render_template("settings.html")


@app.get("/credentials")
def credentials_page():
    return render_template("credentials.html")



@app.get("/clock/display")
def clock_display_page():
    """提供独立的透明环形时钟展示页。"""
    return render_template("clock_display.html")


@app.get("/display/combined")
def combined_display_page():
    return render_template("combined_display.html", display_mode=True, layout_id="")


@app.get("/display/combined/<layout_id>")
def named_combined_display_page(layout_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", layout_id):
        abort(404)
    try:
        _find_display_layout(_load_display_layout_store(), layout_id)
    except Exception:
        abort(404)
    return render_template("combined_display.html", display_mode=True, layout_id=layout_id)


@app.get("/music")
def music_control_page():
    return render_template("music_queue.html")


@app.get("/music/settings")
def music_settings_page():
    return render_template("music_settings.html")


def _queue_display_limit(module_name: str) -> int:
    """读取歌曲/视频展示页各自的待播列表显示条数。"""
    config = ConfigManager().load(module_name, {})
    display = config.get("display", {})
    if not isinstance(display, dict):
        display = {}
    try:
        limit = int(display.get("queue_display_limit", 10))
    except (TypeError, ValueError):
        limit = 10
    return max(1, min(1000, limit))


@app.get("/music/display")
def music_display_page():
    return render_template(
        "music_display.html",
        queue_display_limit=_queue_display_limit("music"),
    )


@app.get("/music/widget")
def music_widget_page():
    """独立歌曲信息展示页。"""
    return render_template("music_widget.html")


@app.get("/video")
def video_control_page():
    return render_template("video_queue.html")


@app.get("/video/settings")
def video_settings_page():
    return render_template("video_settings.html")


@app.get("/video/display")
def video_display_page():
    return render_template(
        "video_display.html",
        queue_display_limit=_queue_display_limit("video"),
    )


@app.get("/api/models")
def api_live2d_models():
    requested = str(request.args.get("version", "3")).strip().lower()
    if requested not in {"3", "moc3"}:
        return jsonify({"ok": False, "error": "该接口仅提供 MOC3 模型列表。"}), 400
    models = [
        {
            "id": item["id"],
            "name": item["name"],
            "config": f"/models/moc3/{item['id']}/model.json",
        }
        for item in _available_models("moc3")
    ]
    return jsonify({"ok": True, "models": models})


@app.get("/api/live2d/settings")
def get_display_settings():
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    try:
        version = _normalize_live2d_version(request.args.get("version") or settings.get("runtime_version") or _live2d_runtime_version())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    available = _available_models(version)
    model_id = settings.get("model_by_version", {}).get(version) or _default_model_id(version)
    requested_model = request.args.get("model")
    if isinstance(requested_model, str) and any(item["id"] == requested_model for item in available):
        model_id = requested_model
    if not any(item["id"] == model_id for item in available):
        model_id = available[0]["id"] if available else ""
    preferences = _model_preferences(settings, model_id, version) if model_id else {}
    profiles = _model_profiles(model_id, version) if model_id else []
    requested_profile = request.args.get("profile")
    valid_profile_ids = {item["id"] for item in profiles}
    if requested_profile in valid_profile_ids:
        preferences["profile"] = requested_profile
    elif preferences.get("profile") not in valid_profile_ids:
        preferences["profile"] = profiles[0]["id"] if profiles else ""
    shortcut_profiles = preferences.get("motion_shortcuts_by_profile", {})
    preferences["motion_shortcuts"] = shortcut_profiles.get(preferences["profile"], {}) if isinstance(shortcut_profiles, dict) else {}
    return jsonify({
        "ok": True,
        "version": version,
        "model": model_id,
        "models": available,
        "preferences": preferences,
        "profiles": profiles,
        "enabled": _live2d_enabled(),
    })


@app.post("/api/live2d/settings")
def save_display_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        version = _normalize_live2d_version(payload.get("version") or request.args.get("version") or _live2d_runtime_version())
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    model_id = payload.get("model")
    if not isinstance(model_id, str) or not any(item["id"] == model_id for item in _available_models(version)):
        return jsonify({"ok": False, "error": "所选模型不存在或缺少有效模型配置。"}), 400
    profile = payload.get("profile")
    available_profiles = _model_profiles(model_id, version)
    if not available_profiles:
        return jsonify({"ok": False, "error": "所选模型没有可用配置。"}), 400
    if not isinstance(profile, str) or not any(item["id"] == profile for item in available_profiles):
        profile = available_profiles[0]["id"]

    def bounded_number(key: str, default: float, low: float, high: float) -> float:
        try:
            value = float(payload.get(key, default))
            if not math.isfinite(value):
                raise ValueError
            return max(low, min(high, value))
        except (TypeError, ValueError):
            raise ValueError(f"{key} 参数无效。")

    def bounded_integer(key: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(payload.get(key, default))))
        except (TypeError, ValueError):
            raise ValueError(f"{key} 参数无效。")

    try:
        preferences = {
            "scale": bounded_number("scale", 1.0, 0.01, 1.5),
            "x": bounded_number("x", 0.0, -1.0, 1.0),
            "y": bounded_number("y", 0.0, -1.0, 1.0),
            "crop_top": bounded_number("crop_top", 0.0, 0.0, 45.0),
            "crop_right": bounded_number("crop_right", 0.0, 0.0, 45.0),
            "crop_bottom": bounded_number("crop_bottom", 0.0, 0.0, 45.0),
            "crop_left": bounded_number("crop_left", 0.0, 0.0, 45.0),
            "bubble_x": bounded_number("bubble_x", 50.0, 0.0, 100.0),
            "bubble_bottom": bounded_number("bubble_bottom", 45.0, 0.0, 95.0),
            "bubble_width": bounded_integer("bubble_width", 420, 240, 760),
            "bubble_max_height": bounded_integer("bubble_max_height", 320, 100, 700),
            "bubble_max_items": bounded_integer("bubble_max_items", 5, 1, 12),
            "bubble_duration": bounded_integer("bubble_duration", 10, 2, 60),
            "bubble_font_size": bounded_integer("bubble_font_size", 20, 12, 40),
        }
        raw_shortcuts = payload.get("motion_shortcuts", {})
        if not isinstance(raw_shortcuts, dict) or len(raw_shortcuts) > 300:
            raise ValueError("动作快捷键配置无效。")
        shortcuts = {}
        used_keys = set()
        for action, key in raw_shortcuts.items():
            if not isinstance(action, str) or len(action) > 256 or "::" not in action:
                raise ValueError("动作快捷键对应的动作标识无效。")
            action_group, action_index = action.rsplit("::", 1)
            # Cubism 3 model3.json 允许使用空字符串作为默认 Motion Group。
            # 这类动作在前端以 "::<index>" 标识，必须允许保存；MOC2 仍要求显式动作组。
            if (not action_group and version != "moc3") or len(action_group) > 200 or not action_index.isdigit() or any(ord(char) < 32 for char in action_group):
                raise ValueError("动作快捷键对应的动作标识无效。")
            if key in (None, ""):
                continue
            if not isinstance(key, str) or not re.fullmatch(r"(?:[A-Z0-9]|F(?:[1-9]|1[0-2]))", key.upper()):
                raise ValueError("快捷键仅支持 0-9、A-Z 或 F1-F12。")
            normalized_key = key.upper()
            if normalized_key in used_keys:
                raise ValueError("快捷键不能重复绑定：" + normalized_key)
            used_keys.add(normalized_key)
            shortcuts[action] = normalized_key
        preferences["motion_shortcuts"] = shortcuts
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with SETTINGS_LOCK:
        settings = _load_user_settings()
        settings.setdefault("model_by_version", {})[version] = model_id
        if version == "moc2":
            settings["model"] = model_id
        preferences["profile"] = profile
        model_settings = settings.setdefault("models", {}).get(f"{version}:{model_id}", {})
        if not isinstance(model_settings, dict):
            model_settings = {}
        shortcut_profiles = model_settings.get("motion_shortcuts_by_profile", {})
        if not isinstance(shortcut_profiles, dict):
            shortcut_profiles = {}
        shortcut_profiles[profile] = preferences.pop("motion_shortcuts", {})
        preferences["motion_shortcuts_by_profile"] = shortcut_profiles
        preferences_to_save = {**model_settings, **preferences}
        settings.setdefault("models", {})[f"{version}:{model_id}"] = preferences_to_save
        _save_user_settings(settings)

    logger.info("已保存 Live2D 展示设置：model=%s bubble_x=%.1f bubble_bottom=%.1f", model_id, preferences["bubble_x"], preferences["bubble_bottom"])
    if payload.get("refresh_obs", True) is False:
        obs_refresh = {"message": "本次保存未刷新 OBS 浏览器源。"}
    else:
        with OBS_DISPLAY_LOCK:
            obs_refresh = _refresh_obs_browser_sources()
    return jsonify({
        "ok": True,
        "version": version,
        "model": model_id,
        "profile": profile,
        "preferences": preferences,
        "obs_refresh": obs_refresh,
    })


@app.get("/models/<model_id>/model.json")
def model_config_file(model_id: str):
    return _serve_model_config(model_id, "moc2")


@app.get("/models/<version>/<model_id>/model.json")
def versioned_model_config_file(version: str, model_id: str):
    try:
        version = _normalize_live2d_version(version)
    except ValueError:
        abort(404)
    return _serve_model_config(model_id, version)


def _serve_model_config(model_id: str, version: str):
    if not _live2d_enabled():
        abort(404)
    profile = request.args.get("profile") if version == "moc2" else None
    try:
        directory, data = _read_model_config(model_id, version, profile)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        abort(404)
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    preferences = _model_preferences(settings, model_id, version)
    if version == "moc2":
        data = _prefix_model_asset_paths(data, _model_asset_prefix(directory, version))
    if version == "moc2":
        layout = data.get("layout")
        if not isinstance(layout, dict):
            layout = {}
            data["layout"] = layout
        original_width = layout.get("width", 2.0)
        original_height = layout.get("height")
        try:
            if isinstance(original_width, (int, float)) and math.isfinite(float(original_width)):
                layout["width"] = float(original_width) * preferences["scale"]
        except (TypeError, ValueError):
            pass
        if isinstance(original_height, (int, float)) and math.isfinite(float(original_height)):
            layout["height"] = float(original_height) * preferences["scale"]
        try:
            layout["center_x"] = float(layout.get("center_x", 0.0)) + preferences["x"]
            layout["center_y"] = float(layout.get("center_y", 0.0)) + preferences["y"]
        except (TypeError, ValueError):
            abort(500)
    # MOC3 的缩放和位置由 Cubism 5 Runtime 的模型矩阵实时处理；这里原样保留
    # model3.json 的 Layout，避免将 Cubism 2 的布局处理重复应用到 MOC3 模型。
    return jsonify(data)


@app.get("/models/<path:filename>")
def model_asset(filename: str):
    if not _live2d_enabled():
        abort(404)
    if not MODEL_ROOT.is_dir():
        abort(404)
    return send_from_directory(MODEL_ROOT, filename, conditional=True)


@app.get("/api/live2d/status")
def get_live2d_status():
    if not _live2d_enabled():
        return jsonify({
            "ok": True,
            "state": "not_started",
            "message": "Live2D 运行时已停用。",
        })
    with RUNTIME_STATE_LOCK:
        state = dict(RUNTIME_STATE)
    return jsonify({"ok": True, **state})


@app.post("/api/live2d/status")
def update_live2d_status():
    if not _live2d_enabled():
        return jsonify({"ok": False, "error": "Live2D 运行时已停用。"}), 409
    payload = request.get_json(silent=True) or {}
    state = payload.get("state")
    message = payload.get("message", "")
    if state not in {"not_started", "loading", "loaded", "error"}:
        return jsonify({"ok": False, "error": "Invalid Live2D state."}), 400
    if not isinstance(message, str):
        message = str(message)
    with RUNTIME_STATE_LOCK:
        RUNTIME_STATE["state"] = state
        RUNTIME_STATE["message"] = message[:500]
    logger.info("Live2D runtime state: %s - %s", state, message[:200])
    return jsonify({"ok": True})


@app.post("/api/bilibili/qr")
def start_qr_login():
    try:
        result = QR_LOGIN.start()
        return jsonify(result)
    except Exception as exc:
        logger.exception("申请 B 站登录二维码失败")
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.get("/api/bilibili/qr")
def poll_qr_login():
    result = QR_LOGIN.poll()
    if result.get("state") == "success":
        with BILIBILI_STATE_LOCK:
            BILIBILI_STATE["logged_in"] = True
            BILIBILI_STATE["message"] = result.get("message", "B 站登录成功。")
        auto_connected, auto_message = _connect_saved_room("二维码登录成功")
        result["auto_connect"] = auto_connected
        result["auto_connect_message"] = auto_message
        with BILIBILI_STATE_LOCK:
            BILIBILI_STATE["message"] = auto_message
    elif result.get("state") in {"expired", "error"}:
        with BILIBILI_STATE_LOCK:
            BILIBILI_STATE["logged_in"] = False
            BILIBILI_STATE["message"] = result.get("message", "二维码登录未完成。")
    return jsonify(result)


@app.get("/api/bilibili/room")
def get_room_setting():
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    return jsonify({"ok": True, "room_id": settings["bilibili"]["room_id"]})


@app.post("/api/bilibili/room")
def save_room_setting():
    payload = request.get_json(silent=True)
    try:
        room_id = int(payload.get("room_id")) if isinstance(payload, dict) else 0
        if room_id <= 0 or room_id > 2_147_483_647:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "请输入有效的 B 站直播间房间号。"}), 400
    # 直播间号统一保存到 config/live.json。
    with SETTINGS_LOCK:
        settings = _load_user_settings()
        settings["bilibili"]["room_id"] = room_id
        _save_user_settings(settings)
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["room_id"] = room_id
    MUSIC_MODULE.update_room_id(room_id)
    VIDEO_MODULE.update_room_id(room_id)
    return jsonify({"ok": True, "room_id": room_id})


@app.post("/api/bilibili/connect")
def connect_live_room():
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    room_id = settings["bilibili"]["room_id"]
    if room_id <= 0:
        return jsonify({"ok": False, "error": "请先在管理界面设置直播间房间号。"}), 400
    valid, message, cookies = _login_status()
    if not valid or not cookies:
        return jsonify({"ok": False, "error": message, "need_login": True}), 401
    try:
        LIVE_CLIENT.start(room_id, cookies)
    except BilibiliClientError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 409
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["room_id"] = room_id
    return jsonify({"ok": True, "message": "连接任务已启动，等待 B 站 WebSocket 认证。"})


@app.post("/api/bilibili/disconnect")
def disconnect_live_room():
    if not LIVE_CLIENT.stop():
        return jsonify({
            "ok": False,
            "error": "正在断开连接，但 WebSocket 工作线程尚未退出；请稍后查看状态，不要重复启动。",
            "connection_state": "stopping",
        }), 409
    return jsonify({"ok": True, "message": "直播间连接已断开。", "connection_state": "disconnected"})





@app.get("/api/modules")
def api_modules():
    return jsonify({"ok": True, "modules": APPLICATION_RUNTIME.modules.list_status()})


@app.post("/api/modules/live2d/version")
def set_live2d_version():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        version = _normalize_live2d_version(payload.get("version"))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    manager = ConfigManager()
    with SETTINGS_LOCK:
        config = manager.load("live2d", {
            "format": "MOC2", "default_model": DEFAULT_MODEL, "enabled": True,
        })
        previous = _normalize_live2d_version(config.get("runtime_version", "moc2"))
        if previous == version:
            return jsonify({
                "ok": True, "version": version,
                "runtime_supported": version == "moc2" or _moc3_runtime_available(),
                "message": "当前已经是所选 Live2D 版本。",
            })
        config["runtime_version"] = version
        try:
            manager.save("live2d", config)
            CONFIG["live2d"] = config
        except Exception as exc:
            logger.exception("保存 Live2D 运行时版本失败。")
            return jsonify({"ok": False, "error": f"保存 Live2D 版本失败：{type(exc).__name__}"}), 500

    with RUNTIME_STATE_LOCK:
        RUNTIME_STATE["state"] = "not_started"
        RUNTIME_STATE["message"] = (
            "Live2D 运行时版本已切换，等待展示页重新加载。"
            if version == "moc2" or _moc3_runtime_available()
            else "已选择 MOC3，但 Cubism 5 R5 运行时文件缺失或不完整；不会回退到 MOC2。"
        )
    with OBS_DISPLAY_LOCK:
        obs_refresh = _refresh_obs_browser_sources()
    return jsonify({
        "ok": True,
        "version": version,
        "runtime_supported": version == "moc2" or _moc3_runtime_available(),
        "obs_refresh": obs_refresh,
        "message": (
            "已切换到 MOC2 运行时。"
            if version == "moc2"
            else (
                "已切换到 MOC3 运行时；展示页将使用 Cubism 5 R5 加载模型。"
                if _moc3_runtime_available()
                else "已保存 MOC3 版本选择，但 Cubism 5 R5 运行时文件缺失或不完整；不会回退到 MOC2。"
            )
        ),
    })


@app.post("/api/modules/live2d/enabled")
def set_live2d_enabled():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
        return jsonify({"ok": False, "error": "enabled 必须是布尔值。"}), 400

    enabled = payload["enabled"]
    current = _live2d_enabled()
    if enabled == current:
        return jsonify({
            "ok": True, "enabled": enabled,
            "modules": APPLICATION_RUNTIME.modules.list_status(),
            "message": "Live2D 运行时状态未变化。",
        })

    manager = ConfigManager()
    with SETTINGS_LOCK:
        config = manager.load("live2d", {
            "format": "MOC2", "default_model": DEFAULT_MODEL, "enabled": True,
        })
        config["enabled"] = enabled
        try:
            manager.save("live2d", config)
            CONFIG["live2d"] = config
        except Exception as exc:
            logger.exception("保存 Live2D 运行时开关失败。")
            return jsonify({"ok": False, "error": f"保存 Live2D 设置失败：{type(exc).__name__}"}), 500

    try:
        action = "start" if enabled else "close"
        APPLICATION_RUNTIME.module_action("live2d", action).result(timeout=30)
        with RUNTIME_STATE_LOCK:
            RUNTIME_STATE["state"] = "not_started"
            RUNTIME_STATE["message"] = (
                "Live2D 运行时已启用，等待展示页加载模型。"
                if enabled else "Live2D 运行时已停用。"
            )
        return jsonify({
            "ok": True,
            "enabled": enabled,
            "modules": APPLICATION_RUNTIME.modules.list_status(),
            "message": "Live2D 运行时已启用。" if enabled else "Live2D 运行时已停用。",
        })
    except Exception as exc:
        logger.exception("切换 Live2D 运行时状态失败。")
        with SETTINGS_LOCK:
            rollback = manager.load("live2d", {
                "format": "MOC2", "default_model": DEFAULT_MODEL, "enabled": True,
            })
            rollback["enabled"] = current
            manager.save("live2d", rollback)
            CONFIG["live2d"] = rollback
        return jsonify({"ok": False, "error": f"切换 Live2D 运行时失败：{type(exc).__name__}"}), 500


def _friendly_module_exception_message(exc: Exception, module_name: str, action: str) -> str:
    """将内部异常转换为面向网页用户的说明；详细异常只保留在服务端日志。"""
    exception_name = type(exc).__name__
    exception_text = str(exc).lower()

    if (
        module_name == "video"
        and action in {"start", "resume"}
        and (
            exception_name == "SessionNotCreatedException"
            or "session not created" in exception_text
        )
    ):
        if "user data directory is already in use" in exception_text or "profile appears to be in use" in exception_text:
            reason = "Chrome 提示专用浏览器配置目录正在被占用，通常是上次运行的本程序 Chrome 尚未完全退出。"
        elif "only supports chrome version" in exception_text:
            reason = "ChromeDriver 与当前安装的 Chrome 版本不匹配。"
        else:
            reason = (
                "可能是上次运行的本程序 Chrome 尚未完全退出、专用浏览器配置目录被占用，"
                "或 Chrome 与 ChromeDriver 版本不兼容。"
            )
        return (
            "视频播放器启动失败：Chrome 无法创建新的浏览器会话。"
            + reason
            + "请关闭本程序残留的 Chrome 后重试；详细错误已记录到程序日志。"
        )

    action_labels = {
        "start": "启动",
        "pause": "暂停",
        "resume": "恢复",
        "close": "关闭",
    }
    module_labels = {
        "video": "视频点播",
        "music": "歌曲点播",
    }
    action_label = action_labels.get(action, "操作")
    module_label = module_labels.get(module_name, "模块")
    if isinstance(exc, TimeoutError) or exception_name in {"TimeoutError", "FutureTimeout"}:
        return f"{module_label}{action_label}等待超时，请检查程序状态后重试；详细原因已记录到程序日志。"
    return f"{module_label}{action_label}失败，请检查程序日志中的详细错误后重试。"


@app.post("/api/modules/<module_name>/<action>")
def api_module_action(module_name: str, action: str):
    if action not in {"start", "pause", "resume", "close"}:
        return jsonify({"ok": False, "error": "不支持的模块操作。"}), 400
    if module_name not in APPLICATION_RUNTIME.modules.list_status():
        return jsonify({"ok": False, "error": "模块不存在或尚未迁移。"}), 404
    if module_name == "live2d":
        return jsonify({"ok": False, "error": "Live2D 运行时请通过接入中心的统一开关控制。"}), 409
    if action in {"start", "resume"}:
        with BILIBILI_STATE_LOCK:
            connected = BILIBILI_STATE["connected"]
        if not connected and module_name != "live2d":
            return jsonify({"ok": False, "error": "请先完成 B 站 WebSocket 认证连接，再启动互动模块。"}), 409
    try:
        future = APPLICATION_RUNTIME.module_action(module_name, action)
        timeout = 120 if module_name == "video" and action in {"start", "close"} else 30
        future.result(timeout=timeout)
        return jsonify({
            "ok": True,
            "module": module_name,
            "action": action,
            "modules": APPLICATION_RUNTIME.modules.list_status(),
        })
    except Exception as exc:
        logger.exception("模块控制失败：module=%s action=%s", module_name, action)
        return jsonify({"ok": False, "error": _friendly_module_exception_message(exc, module_name, action)}), 500


@app.get("/api/music/queue")
def api_music_queue():
    try:
        snapshot = APPLICATION_RUNTIME.async_runtime.submit(MUSIC_MODULE.queue_snapshot()).result(timeout=5)
        return jsonify(snapshot)
    except Exception as exc:
        logger.exception("读取歌曲队列快照失败。")
        return jsonify({"ok": False, "error": "暂时无法读取歌曲点播队列，请检查程序日志中的详细错误。"}), 503

@app.get("/api/music/widget")
def api_music_widget():
    """提供洛雪当前播放歌曲信息，供封面/歌名/歌手展示组件使用。"""
    try:
        status = get_lx_status()
        if not isinstance(status, dict):
            return jsonify({
                "ok": True,
                "available": False,
                "playing": False,
                "name": "",
                "singer": "",
                "albumName": "",
                "duration": 0,
                "progress": 0,
                "playbackRate": 1,
                "picUrl": "",
                "lyricLineText": "",
            })

        result = dict(status)
        result["ok"] = True
        result["available"] = True
        result["playing"] = is_lx_playing(status)
        result["name"] = str(status.get("name", "") or "")
        result["singer"] = str(status.get("singer", "") or "")
        result["albumName"] = str(status.get("albumName", "") or "")

        # 兼容洛雪不同版本/音源返回的封面字段，避免只返回歌名和歌手。
        cover_keys = ("picUrl", "pic_url", "img", "coverUrl", "cover_url", "cover", "image", "albumPic")
        cover_value = ""
        pending = [status]
        visited = set()
        while pending and not cover_value:
            current_obj = pending.pop(0)
            if not isinstance(current_obj, dict) or id(current_obj) in visited:
                continue
            visited.add(id(current_obj))
            for key in cover_keys:
                value = current_obj.get(key)
                if isinstance(value, str) and value.strip():
                    cover_value = value.strip()
                    break
            if not cover_value:
                for value in current_obj.values():
                    if isinstance(value, dict):
                        pending.append(value)
                    elif isinstance(value, list):
                        pending.extend(item for item in value if isinstance(item, dict))

        if cover_value.startswith("//"):
            cover_value = "https:" + cover_value
        result["picUrl"] = cover_value
        if urlparse(result["picUrl"]).scheme not in ("http", "https"):
            result["picUrl"] = ""
        return jsonify(result)
    except Exception as exc:
        logger.exception("读取歌曲信息组件数据失败。")
        return jsonify({"ok": False, "error": "暂时无法读取当前歌曲信息，请检查程序日志中的详细错误。"}), 503


@app.get("/api/video/queue")
def api_video_queue():
    try:
        snapshot = APPLICATION_RUNTIME.async_runtime.submit(VIDEO_MODULE.queue_snapshot()).result(timeout=5)
        return jsonify(snapshot)
    except Exception as exc:
        logger.exception("读取视频队列快照失败。")
        return jsonify({"ok": False, "error": "暂时无法读取视频点播队列，请检查程序日志中的详细错误。"}), 503


@app.post("/api/video/action")
def api_video_action():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "message": "请求内容无效。"}), 400
    try:
        future = APPLICATION_RUNTIME.async_runtime.submit(VIDEO_MODULE.panel_action(payload))
        result = future.result(timeout=60)
        return jsonify(result), (200 if result.get("ok") else 400)
    except Exception as exc:
        logger.exception("视频模块队列操作失败。")
        return jsonify({"ok": False, "message": _friendly_module_exception_message(exc, "video", "action")}), 500


@app.post("/api/music/action")
def api_music_action():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "message": "请求内容无效。"}), 400
    try:
        future = APPLICATION_RUNTIME.async_runtime.submit(MUSIC_MODULE.panel_action(payload))
        result = future.result(timeout=25)
        return jsonify(result), (200 if result.get("ok") else 400)
    except Exception as exc:
        logger.exception("歌曲模块队列操作失败。")
        return jsonify({"ok": False, "message": _friendly_module_exception_message(exc, "music", "action")}), 500



def _default_display_layout() -> dict[str, Any]:
    return {
        "version": 1,
        "widgets": [
            {"id": "live2d", "type": "live2d", "x": 0, "y": 0, "w": 45, "h": 100},
            {"id": "music", "type": "music", "x": 45, "y": 0, "w": 27.5, "h": 50},
            {"id": "video", "type": "video", "x": 72.5, "y": 0, "w": 27.5, "h": 50},
            {"id": "voice", "type": "voice", "x": 45, "y": 50, "w": 27.5, "h": 50},
            {"id": "danmaku", "type": "danmaku", "x": 72.5, "y": 50, "w": 27.5, "h": 50},
        ],
    }


def _sanitize_display_layout(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("widgets"), list):
        raise ValueError("展示布局格式无效。")
    widgets = []
    used_types = set()
    for item in payload["widgets"][:10]:
        if not isinstance(item, dict):
            continue
        widget_type = str(item.get("type", ""))
        if widget_type not in DISPLAY_WIDGET_TYPES or widget_type in used_types:
            continue
        try:
            x, y, w, h = (float(item.get(k, d)) for k, d in (("x", 0), ("y", 0), ("w", 30), ("h", 30)))
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(v) for v in (x, y, w, h)):
            continue
        w = max(12.0, min(100.0, w))
        h = max(12.0, min(100.0, h))
        x = max(0.0, min(100.0 - w, x))
        y = max(0.0, min(100.0 - h, y))
        try:
            font_scale = float(item.get("font_scale", 100))
            content_scale = float(item.get("content_scale", 100))
        except (TypeError, ValueError):
            font_scale, content_scale = 100.0, 100.0
        if not math.isfinite(font_scale):
            font_scale = 100.0
        if not math.isfinite(content_scale):
            content_scale = 100.0
        font_scale = max(50.0, min(200.0, font_scale))
        content_scale = max(50.0, min(200.0, content_scale))
        widgets.append({
            "id": widget_type, "type": widget_type,
            "x": round(x, 2), "y": round(y, 2),
            "w": round(w, 2), "h": round(h, 2),
            "font_scale": round(font_scale, 1),
            "content_scale": round(content_scale, 1),
        })
        used_types.add(widget_type)
    return {"version": 1, "widgets": widgets}


DISPLAY_LAYOUTS_PATH = BASE_DIR / "data" / "display_layouts.json"
DISPLAY_LAYOUT_LOCK = threading.RLock()
DEFAULT_DISPLAY_LAYOUT_ID = "default"


def _write_display_layout_store(store: dict[str, Any]) -> None:
    DISPLAY_LAYOUTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = DISPLAY_LAYOUTS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, DISPLAY_LAYOUTS_PATH)


def _load_display_layout_store() -> dict[str, Any]:
    """读取多布局仓库；首次运行时创建默认布局。"""
    with DISPLAY_LAYOUT_LOCK:
        if DISPLAY_LAYOUTS_PATH.is_file():
            raw = json.loads(DISPLAY_LAYOUTS_PATH.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or not isinstance(raw.get("layouts"), list):
                raise ValueError("综合展示布局集合格式无效。")
            layouts = []
            seen = set()
            for item in raw["layouts"]:
                if not isinstance(item, dict):
                    continue
                layout_id = str(item.get("id", ""))
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", layout_id) or layout_id in seen:
                    continue
                try:
                    clean_layout = _sanitize_display_layout(item.get("layout"))
                except Exception:
                    continue
                name = str(item.get("name", "未命名布局")).strip()[:40] or "未命名布局"
                layouts.append({"id": layout_id, "name": name, "layout": clean_layout})
                seen.add(layout_id)
            if not layouts:
                layouts = [{"id": DEFAULT_DISPLAY_LAYOUT_ID, "name": "默认布局", "layout": _default_display_layout()}]
            active_id = str(raw.get("active_layout_id", layouts[0]["id"]))
            if active_id not in seen:
                active_id = layouts[0]["id"]
            return {"version": 1, "active_layout_id": active_id, "layouts": layouts}

        store = {"version": 1, "active_layout_id": DEFAULT_DISPLAY_LAYOUT_ID,
                 "layouts": [{"id": DEFAULT_DISPLAY_LAYOUT_ID, "name": "默认布局", "layout": _default_display_layout()}]}
        _write_display_layout_store(store)
        return store


def _find_display_layout(store: dict[str, Any], layout_id: str | None = None) -> dict[str, Any]:
    target = layout_id or store["active_layout_id"]
    for item in store["layouts"]:
        if item["id"] == target:
            return item
    raise KeyError("指定的综合展示布局不存在。")


def _save_display_layout_store(store: dict[str, Any]) -> None:
    _write_display_layout_store(store)


@app.get("/api/music/settings")
def get_music_settings():
    config = ConfigManager().load("music", {})
    lx = config.get("lx_music", {})
    search = config.get("search", {})
    requests_config = config.get("requests", {})
    display_config = config.get("display", {})
    return jsonify({
        "ok": True,
        "lx_music": {
            "base_url": lx.get("base_url", "http://127.0.0.1:23330"),
            "timeout_seconds": lx.get("timeout_seconds", 5),
        },
        "search": {
            "max_results_per_platform": search.get("max_results_per_platform", 20),
            "enabled_platforms": search.get("enabled_platforms", ["tx", "kg", "wy", "kw"]),
        },
        "requests": {
            "cooldown_seconds": requests_config.get("cooldown_seconds", 10),
            "queue_limit": requests_config.get("queue_limit", 100),
            "require_fans_medal": bool(requests_config.get("require_fans_medal", False)),
        },
        "display": {
            "queue_display_limit": display_config.get("queue_display_limit", 10),
        },
        "module_state": APPLICATION_RUNTIME.modules.list_status().get("music", {}).get("state", "stopped"),
    })


@app.post("/api/music/settings")
def save_music_settings():
    if APPLICATION_RUNTIME.modules.list_status().get("music", {}).get("state") != "stopped":
        return jsonify({"ok": False, "error": "请先关闭歌曲模块，再修改配置。"}), 409
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        config = ConfigManager().load("music", {})
        lx = config.setdefault("lx_music", {})
        search = config.setdefault("search", {})
        req = config.setdefault("requests", {})
        display = config.setdefault("display", {})
        base_url = str(payload.get("base_url", lx.get("base_url", ""))).strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("洛雪 API 地址必须以 http:// 或 https:// 开头。")
        timeout = int(payload.get("timeout_seconds", lx.get("timeout_seconds", 5)))
        max_results = int(payload.get("max_results_per_platform", search.get("max_results_per_platform", 20)))
        allowed_platforms = ("tx", "kg", "wy", "kw")
        raw_platforms = payload.get(
            "enabled_platforms",
            search.get("enabled_platforms", list(allowed_platforms)),
        )
        if not isinstance(raw_platforms, list):
            raise ValueError("查询平台必须是列表。")
        enabled_platforms = [key for key in allowed_platforms if key in raw_platforms]
        if len(enabled_platforms) != len(set(raw_platforms)) or any(key not in allowed_platforms for key in raw_platforms):
            raise ValueError("查询平台选项无效。")
        if not enabled_platforms:
            raise ValueError("至少选择一个音乐查询平台。")
        cooldown = int(payload.get("cooldown_seconds", req.get("cooldown_seconds", 10)))
        queue_limit = int(payload.get("queue_limit", req.get("queue_limit", 100)))
        display_limit = int(payload.get("queue_display_limit", display.get("queue_display_limit", 10)))
        raw_require_medal = payload.get("require_fans_medal", req.get("require_fans_medal", False))
        if isinstance(raw_require_medal, bool):
            require_medal = raw_require_medal
        elif raw_require_medal in (0, 1, "0", "1"):
            # 兼容旧页面/旧 API：0=要求粉丝牌，1=不要求粉丝牌。
            require_medal = str(raw_require_medal) == "0"
        elif isinstance(raw_require_medal, str) and raw_require_medal.strip().lower() in {"true", "false"}:
            require_medal = raw_require_medal.strip().lower() == "true"
        else:
            raise ValueError("粉丝牌选项必须是布尔值。")
        if not 2 <= timeout <= 30 or not 1 <= max_results <= 50:
            raise ValueError("接口超时应为 2–30 秒，单平台搜索数量应为 1–50。")
        if not 0 <= cooldown <= 3600 or not 1 <= queue_limit <= 1000 or not 1 <= display_limit <= 1000:
            raise ValueError("冷却时间、队列上限或展示页队列显示数量无效。")
        lx.update({"base_url": base_url, "timeout_seconds": timeout})
        search["max_results_per_platform"] = max_results
        search["enabled_platforms"] = enabled_platforms
        req.update({
            "cooldown_seconds": cooldown,
            "queue_limit": queue_limit,
            "require_fans_medal": bool(require_medal),
        })
        display["queue_display_limit"] = display_limit
        ConfigManager().save("music", config)
        runtime_config = dict(config)
        runtime_config["admin_uids"] = _load_system_admin_uids()
        MUSIC_MODULE.reload_config(runtime_config)
        return jsonify({"ok": True, "message": "歌曲模块配置已保存。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "歌曲配置保存失败。"}), 400


@app.get("/api/video/settings")
def get_video_settings():
    config = ConfigManager().load("video", {})
    chrome = config.get("chrome", {})
    req = config.get("requests", {})
    display = config.get("display", {})
    return jsonify({
        "ok": True,
        "chrome": {
            "binary_path": chrome.get("binary_path", ""),
            "profile_dir": chrome.get("profile_dir", "data/video/chrome_profile"),
            "page_load_timeout_seconds": chrome.get("page_load_timeout_seconds", 45),
        },
        "requests": {
            "cooldown_seconds": req.get("cooldown_seconds", 60),
            "queue_limit": req.get("queue_limit", 30),
            "max_video_duration_seconds": req.get("max_video_duration_seconds", 300),
            "require_fan_medal": bool(req.get("require_fans_medal", False)),
        },
        "display": {
            "queue_display_limit": display.get("queue_display_limit", 10),
        },
        "module_state": APPLICATION_RUNTIME.modules.list_status().get("video", {}).get("state", "stopped"),
    })


@app.post("/api/video/settings")
def save_video_settings():
    if APPLICATION_RUNTIME.modules.list_status().get("video", {}).get("state") != "stopped":
        return jsonify({"ok": False, "error": "请先关闭视频模块，再修改配置。"}), 409
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        config = ConfigManager().load("video", {})
        chrome = config.setdefault("chrome", {})
        req = config.setdefault("requests", {})
        display = config.setdefault("display", {})
        binary_path = str(payload.get("binary_path", chrome.get("binary_path", ""))).strip()
        profile_dir = str(payload.get("profile_dir", chrome.get("profile_dir", "data/video/chrome_profile"))).strip()
        if "\n" in binary_path or "\r" in binary_path or not profile_dir or "\n" in profile_dir or "\r" in profile_dir:
            raise ValueError("Chrome 路径或 Profile 目录无效。")
        timeout = int(payload.get("page_load_timeout_seconds", chrome.get("page_load_timeout_seconds", 45)))
        cooldown = int(payload.get("cooldown_seconds", req.get("cooldown_seconds", 60)))
        queue_limit = int(payload.get("queue_limit", req.get("queue_limit", 30)))
        display_limit = int(payload.get("queue_display_limit", display.get("queue_display_limit", 10)))
        max_duration = int(payload.get("max_video_duration_seconds", req.get("max_video_duration_seconds", 300)))
        require_medal = bool(payload.get("require_fan_medal", req.get("require_fans_medal", False)))
        if not 10 <= timeout <= 180 or not 0 <= cooldown <= 3600:
            raise ValueError("页面加载超时应为 10–180 秒，冷却时间应为 0–3600 秒。")
        if not 1 <= queue_limit <= 1000 or not 1 <= display_limit <= 1000 or not 1 <= max_duration <= 3600:
            raise ValueError("视频队列上限、展示页队列显示数量或视频时长限制无效。")
        chrome.update({
            "binary_path": binary_path,
            "profile_dir": profile_dir,
            "page_load_timeout_seconds": timeout,
        })
        req.update({
            "cooldown_seconds": cooldown,
            "queue_limit": queue_limit,
            "max_video_duration_seconds": max_duration,
            "require_fans_medal": require_medal,
        })
        display["queue_display_limit"] = display_limit
        ConfigManager().save("video", config)
        runtime_config = dict(config)
        runtime_config["admin_uids"] = _load_system_admin_uids()
        VIDEO_MODULE.reload_config(runtime_config)
        return jsonify({"ok": True, "message": "视频模块配置已保存。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "视频配置保存失败。"}), 400


@app.get("/api/credentials/status")
def get_credentials_status():
    valid, message, _ = _login_status()
    obs_status = CredentialStore().status("obs")
    return jsonify({
        "ok": True,
        "bilibili": {"configured": bool(valid), "message": message},
        "obs": {"configured": bool(obs_status.get("configured"))},
    })


@app.get("/api/settings/admins")
def get_system_admin_settings():
    return jsonify({"ok": True, "admin_uids": _load_system_admin_uids()})


@app.post("/api/settings/admins")
def save_system_admin_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("admin_uids"), list):
        return jsonify({"ok": False, "error": "管理员 UID 必须以列表形式提交。"}), 400
    try:
        admin_uids = []
        for value in payload["admin_uids"]:
            raw = str(value).strip()
            if not raw.isdigit() or int(raw) <= 0:
                raise ValueError(f"管理员 UID 无效：{raw or '空值'}")
            uid = int(raw)
            if uid not in admin_uids:
                admin_uids.append(uid)
        if len(admin_uids) > 100:
            raise ValueError("系统管理员最多设置 100 个 UID。")
        app_config = ConfigManager().load("app", {})
        app_config["admin_uids"] = sorted(admin_uids)
        ConfigManager().save("app", app_config)

        # 运行中的模块不必重启：同步更新两套模块的权限集合。
        music_config = _module_config_with_system_admins("music")
        music_config["admin_uids"] = sorted(admin_uids)
        if not MUSIC_MODULE.bot.enabled:
            MUSIC_MODULE.reload_config(music_config)
        else:
            MUSIC_MODULE.bot.admin_uids = set(admin_uids)
            MUSIC_MODULE.config = music_config

        video_config = _module_config_with_system_admins("video")
        video_config["admin_uids"] = sorted(admin_uids)
        if not __import__("modules.video.service", fromlist=["module_enabled"]).module_enabled:
            VIDEO_MODULE.reload_config(video_config)
        else:
            from modules.video import service as video_service
            video_service.ADMIN_UIDS = set(admin_uids)
            VIDEO_MODULE.config = video_config
        return jsonify({"ok": True, "message": "系统管理员 UID 已保存，歌曲和视频模块已同步。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "系统管理员 UID 保存失败。"}), 400


@app.get("/api/settings/obs")
def get_obs_settings():
    config = ConfigManager().load("obs", {})
    return jsonify({
        "ok": True,
        "host": config.get("host", "127.0.0.1"),
        "port": config.get("port", 4455),
        "timeout": config.get("timeout", 2.5),
        "source_name": config.get("source_name", "b站视频播放器"),
        "password_configured": bool(CredentialStore().status("obs").get("configured")),
    })


@app.post("/api/settings/obs")
def save_obs_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        host = str(payload.get("host", "")).strip()
        port = int(payload.get("port", 4455))
        timeout = max(0.5, min(10.0, float(payload.get("timeout", 2.5))))
        source_name = str(payload.get("source_name", "")).strip()
        if not host or not 1 <= port <= 65535 or not source_name:
            raise ValueError("OBS 地址、有效端口和来源名称不能为空。")
        config = {"host": host, "port": port, "timeout": timeout,
                  "source_name": source_name}
        ConfigManager().save("obs", config)
        CONFIG["obs_websocket"] = {
            "host": host, "port": port, "timeout": timeout,
        }
        VIDEO_MODULE.reload_obs_settings()
        _refresh_obs_browser_sources()
        return jsonify({"ok": True, "message": "OBS 普通配置已保存。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "OBS 配置保存失败。"}), 400


@app.post("/api/credentials/obs")
def save_obs_credential():
    payload = request.get_json(silent=True)
    password = payload.get("password") if isinstance(payload, dict) else None
    if not isinstance(password, str) or not password.strip():
        return jsonify({"ok": False, "error": "OBS WebSocket 密码不能为空。"}), 400
    try:
        CredentialStore().save("obs", {"password": password})
        VIDEO_MODULE.reload_obs_settings()
        _refresh_obs_browser_sources()
        return jsonify({"ok": True, "message": "OBS 密码已加密保存，不会回显密码原文。"})
    except Exception as exc:
        logger.warning("保存 OBS 凭据失败：%s", type(exc).__name__)
        return jsonify({"ok": False, "error": "OBS 密码保存失败，请检查本机凭据存储权限。"}), 500


@app.get("/api/settings/logging")
def get_logging_settings():
    config = ConfigManager().load("logging", {})
    return jsonify({
        "ok": True,
        "debug": config.get("debug") is True,
        "retention_days": int(config.get("retention_days", 7)),
        "retention_options": config.get("retention_options", [3, 5, 7, 15, 30, 60, 90, 0]),
    })


@app.post("/api/settings/logging")
def save_logging_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        retention = int(payload.get("retention_days", 7))
        debug = payload.get("debug", False)
        if not isinstance(debug, bool):
            raise ValueError("DEBUG 开关必须为布尔值。")
        allowed_retention = {3, 5, 7, 15, 30, 60, 90, 0}
        if retention not in allowed_retention:
            raise ValueError("日志保留天数无效。")
        config = ConfigManager().load("logging", {})
        config.pop("daily_rotation", None)
        config.pop("level", None)
        config.update({
            "debug": debug,
            "retention_days": retention,
            "retention_options": [3, 5, 7, 15, 30, 60, 90, 0],
        })
        ConfigManager().save("logging", config)
        setup_logging()
        logger.info("日志设置已更新：debug=%s retention_days=%s", debug, retention)
        return jsonify({"ok": True, "message": "日志设置已保存并立即生效。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "日志设置保存失败。"}), 400


@app.get("/api/display/layout/default")
def get_default_combined_display_layout():
    return jsonify({"ok": True, "layout": _default_display_layout()})


@app.get("/api/display/layouts")
def get_combined_display_layouts():
    try:
        store = _load_display_layout_store()
        return jsonify({"ok": True, "active_layout_id": store["active_layout_id"],
                        "layouts": [{"id": x["id"], "name": x["name"]} for x in store["layouts"]]})
    except Exception as exc:
        logger.exception("读取综合展示布局列表失败。")
        return jsonify({"ok": False, "error": str(exc) or "读取布局列表失败。"}), 500


@app.post("/api/display/layouts")
def create_combined_display_layout():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    name = str(payload.get("name", "")).strip()[:40]
    if not name:
        return jsonify({"ok": False, "error": "请填写布局名称。"}), 400
    try:
        with DISPLAY_LAYOUT_LOCK:
            store = _load_display_layout_store()
            source_id = str(payload.get("source_layout_id", ""))
            layout = _sanitize_display_layout(_find_display_layout(store, source_id)["layout"]) if source_id else {"version": 1, "widgets": []}
            layout_id = uuid.uuid4().hex[:12]
            store["layouts"].append({"id": layout_id, "name": name, "layout": layout})
            _save_display_layout_store(store)
        return jsonify({"ok": True, "layout_id": layout_id, "name": name, "message": "新布局已创建。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "创建布局失败。"}), 400


@app.patch("/api/display/layouts/<layout_id>")
def update_combined_display_layout_metadata(layout_id: str):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        with DISPLAY_LAYOUT_LOCK:
            store = _load_display_layout_store()
            item = _find_display_layout(store, layout_id)
            if "name" in payload:
                name = str(payload.get("name", "")).strip()[:40]
                if not name:
                    raise ValueError("布局名称不能为空。")
                item["name"] = name
            if payload.get("activate") is True:
                store["active_layout_id"] = layout_id
            _save_display_layout_store(store)
        return jsonify({"ok": True, "active_layout_id": store["active_layout_id"], "message": "布局信息已更新。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "更新布局失败。"}), 400


@app.delete("/api/display/layouts/<layout_id>")
def delete_combined_display_layout(layout_id: str):
    try:
        with DISPLAY_LAYOUT_LOCK:
            store = _load_display_layout_store()
            if len(store["layouts"]) <= 1:
                raise ValueError("至少需要保留一套布局。")
            _find_display_layout(store, layout_id)
            store["layouts"] = [x for x in store["layouts"] if x["id"] != layout_id]
            if store["active_layout_id"] == layout_id:
                store["active_layout_id"] = store["layouts"][0]["id"]
            _save_display_layout_store(store)
        return jsonify({"ok": True, "active_layout_id": store["active_layout_id"], "message": "布局已删除。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "删除布局失败。"}), 400


@app.get("/api/display/layout")
def get_combined_display_layout():
    try:
        store = _load_display_layout_store()
        item = _find_display_layout(store, request.args.get("layout_id"))
        return jsonify({"ok": True, "layout_id": item["id"], "name": item["name"], "layout": item["layout"]})
    except KeyError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    except Exception as exc:
        logger.warning("读取综合展示布局失败：%s", type(exc).__name__)
        return jsonify({"ok": False, "error": "读取综合展示布局失败。"}), 500


@app.post("/api/display/layout")
def save_combined_display_layout():
    payload = request.get_json(silent=True)
    try:
        if not isinstance(payload, dict):
            raise ValueError("请求内容必须是 JSON 对象。")
        layout = _sanitize_display_layout(payload.get("layout", payload))
        layout_id = str(payload.get("layout_id", "")).strip()
        with DISPLAY_LAYOUT_LOCK:
            store = _load_display_layout_store()
            item = _find_display_layout(store, layout_id or None)
            item["layout"] = layout
            _save_display_layout_store(store)

        # 布局落盘后异步刷新 OBS 综合展示源；OBS 未连接时不影响保存结果。
        _start_obs_refresh_thread("obs-layout-save-refresh")
        return jsonify({"ok": True, "layout_id": item["id"], "layout": layout,
                        "message": "布局“{}”已保存。".format(item["name"])})
    except Exception as exc:
        logger.warning("保存综合展示布局失败：%s", type(exc).__name__)
        return jsonify({"ok": False, "error": str(exc) or "布局保存失败。"}), 400


@app.get("/api/danmaku")
def get_danmaku():
    with DANMAKU_LOCK:
        if request.args.get("latest") == "1":
            return jsonify({"ok": True, "items": [], "latest_id": DANMAKU_SEQUENCE})
        try:
            after = max(0, int(request.args.get("after", "0")))
        except ValueError:
            after = 0
        items = [item for item in DANMAKU_ITEMS if item["id"] > after]
    return jsonify({"ok": True, "items": items})


@app.get("/api/status")
def api_status():
    live2d = model_status()
    with BILIBILI_STATE_LOCK:
        bili = dict(BILIBILI_STATE)
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    bili["room_id"] = settings["bilibili"]["room_id"]
    return jsonify({
        "application": "Bili Live Danmaku",
        "web_server": "running",
        "bilibili": bili,
        "live2d": live2d,
        "features": {
            "danmaku": True,
            "gift_events": True,
            "tts": True,
            "mouth_sync": True,
            "live2d_runtime": live2d["enabled"],
            "moc2_runtime": live2d["enabled"] and live2d["version"] == "moc2" and live2d["runtime_ready"],
            "moc3_runtime": live2d["enabled"] and live2d["version"] == "moc3" and live2d["runtime_ready"],
            "music": True,
            "video": True,
        },
        "modules": APPLICATION_RUNTIME.modules.list_status(),
    })


@app.get("/api/extensions/lol-hex")
def api_lol_hex_status():
    return jsonify(LOL_HEX_EXTENSION.status())


@app.get("/api/extensions/lol-hex/client-status")
def api_lol_hex_client_status():
    """Return the same League Client / LCU recognition status shown by the Hex manager page."""
    try:
        return jsonify(LOL_HEX_EXTENSION._request("/api/client-status", timeout=1.5))
    except Exception as exc:
        return jsonify({
            "recognized": False,
            "available": False,
            "source": "",
            "account": "",
            "message": f"无法读取 LOL 海克斯客户端状态：{str(exc) or '服务暂不可用'}",
        }), 503


@app.post("/api/extensions/lol-hex/start")
def api_lol_hex_start():
    try:
        result = LOL_HEX_EXTENSION.start()
        if result.get("running"):
            _start_obs_refresh_thread("obs-lol-hex-start-refresh")
        return jsonify(result)
    except Exception as exc:
        logger.exception("启动 LOL Hex 扩展失败。")
        return jsonify({"ok": False, "error": str(exc) or "LOL Hex 启动失败。"}), 500


@app.post("/api/extensions/lol-hex/stop")
def api_lol_hex_stop():
    try:
        result = LOL_HEX_EXTENSION.stop()
        _start_obs_refresh_thread("obs-lol-hex-stop-refresh")
        return jsonify(result)
    except Exception as exc:
        logger.exception("停止 LOL Hex 扩展失败。")
        return jsonify({"ok": False, "error": str(exc) or "LOL Hex 停止失败。"}), 500


@app.get("/extensions/lol-hex/logs")
def lol_hex_extension_logs():
    log_dir = LOL_HEX_EXTENSION.extension_dir / "logs"
    try:
        candidates = sorted(
            (
                item for item in log_dir.glob("*.log")
                if re.fullmatch(r"(?:app_)?\d{4}-\d{2}-\d{2}\.log", item.name)
                and item.is_file()
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        candidates = []
    if not candidates:
        return "LOL Hex 暂无日志文件。", 404
    return send_from_directory(log_dir, candidates[0].name, mimetype="text/plain")


@app.get("/extensions/lol-hex/")
@app.get("/extensions/lol-hex/<path:resource>")
def lol_hex_extension_redirect(resource: str = ""):
    if resource not in {"", "bar", "display"}:
        abort(404)
    host = LOL_HEX_EXTENSION.host
    if ":" in host:
        host = f"[{host}]"
    suffix = f"/{resource}" if resource else "/"
    return redirect(f"http://{host}:{LOL_HEX_EXTENSION.port}{suffix}")


def _initialize_login_state() -> None:
    valid, message, _ = _login_status()
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["message"] = message


def _credential_watchdog() -> None:
    while not CREDENTIAL_WATCHDOG_STOP.wait(60.0):
        with BILIBILI_STATE_LOCK:
            should_check = BILIBILI_STATE["connection_state"] in {"connected", "connecting", "reconnecting"}
        if should_check:
            valid, message, _ = _login_status()
            if not valid:
                logger.warning("B站登录凭据状态检查失败（可能是网络波动）；当前连接仍由 WebSocket 状态回调管理：%s", message)


_initialize_login_state()


_CONSOLE_CTRL_HANDLER = None


def _install_console_close_handler() -> None:
    """捕获 Windows CMD 右上角关闭事件；该事件不保证执行 Python finally/atexit。"""
    global _CONSOLE_CTRL_HANDLER
    if os.name != "nt":
        return

    handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def handle_console_event(event_type: int) -> bool:
        # CTRL_CLOSE_EVENT=2，CTRL_LOGOFF_EVENT=5，CTRL_SHUTDOWN_EVENT=6。
        if event_type not in {2, 5, 6}:
            return False
        logger.warning("检测到 Windows 控制台关闭事件，开始执行紧急资源清理。")

        # 退出时只清理本程序启动的 Chrome；不向洛雪发送任何控制请求，
        # 不恢复、不暂停、不停止，也不关闭洛雪播放器。
        task = threading.Thread(
            target=_shutdown_application_and_refresh_obs,
            name="emergency-application-shutdown",
            daemon=True,
        )
        task.start()
        # 退出处理线程负责：关闭全部模块 -> 停止直播连接 -> 最后刷新 OBS 综合展示源。
        task.join(65.0)
        logger.warning(
            "控制台关闭事件清理结束：退出清理线程存活=%s；OBS最终刷新=%s。",
            task.is_alive(), APPLICATION_SHUTDOWN_FINISHED.is_set(),
        )
        return True

    _CONSOLE_CTRL_HANDLER = handler_type(handle_console_event)
    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_CONSOLE_CTRL_HANDLER, True):
        logger.warning("注册 Windows 控制台关闭事件处理器失败。")
    else:
        logger.info("已注册 Windows CMD 关闭事件清理处理器。")


def _startup_port_is_available(host: str, port: int) -> bool:
    """在启动任何后台模块前检查本地管理服务端口是否可绑定。"""
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        logger.error("无法解析管理服务监听地址 %s：%s", host, exc)
        return False

    if not addresses:
        logger.error("无法解析管理服务监听地址：%s", host)
        return False

    # 与 Werkzeug 的常规监听方式一致，先检查系统解析出的首个监听地址。
    family, socktype, proto, _, sockaddr = addresses[0]
    try:
        with socket.socket(family, socktype, proto) as probe:
            probe.bind(sockaddr)
    except OSError as exc:
        logger.error(
            "启动失败：管理服务端口 %s（监听地址 %s）已被占用或无法使用：%s。"
            "请先关闭已经运行的 B站直播弹幕助手，再重新启动。",
            port, host, exc,
        )
        return False
    return True


if __name__ == "__main__":
    host = CONFIG["app"].get("host", "127.0.0.1")
    port = int(CONFIG["app"].get("port", 8768))
    debug = bool(CONFIG["app"].get("debug", False))

    # 必须先确认端口可用，再启动后台运行时、B站连接和 OBS 刷新线程。
    if not _startup_port_is_available(host, port):
        raise SystemExit(1)

    APPLICATION_RUNTIME.start()
    _install_console_close_handler()
    CREDENTIAL_WATCHDOG_THREAD = threading.Thread(
        target=_credential_watchdog, name="bili-credential-watchdog", daemon=True
    )
    CREDENTIAL_WATCHDOG_THREAD.start()
    atexit.register(_shutdown_application_and_refresh_obs)
    _restore_saved_live_connection()
    OBS_COMBINED_REFRESH_THREAD = threading.Thread(
        target=_obs_combined_display_refresh_watchdog,
        name="obs-combined-display-refresh",
        daemon=True,
    )
    OBS_COMBINED_REFRESH_THREAD.start()
    logger.info("Starting local management server at http://%s:%s", host, port)
    try:
        app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)
    finally:
        # 与 CMD 关闭事件共用同一退出流程，保证所有模块关闭后再刷新 OBS 综合展示源。
        _shutdown_application_and_refresh_obs()