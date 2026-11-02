# -*- coding: utf-8 -*-
"""Bili Live Danmaku application entry point."""
from __future__ import annotations

import atexit
import asyncio
import base64
import hashlib
import json
import logging
import math
import os
import re
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
from core.logger_manager import setup_logging
from modules.music.service import MusicModuleFacade
from modules.video.service import VideoModuleFacade
from modules.live2d.service import Live2DModule
from modules.voice.service import VoiceModule

BASE_DIR = get_base_dir()
LEGACY_CONFIG_PATH = BASE_DIR / "config.json"
MODEL_ROOT = BASE_DIR / "models"
USER_SETTINGS_PATH = BASE_DIR / "user_settings.json"
DISPLAY_LAYOUT_PATH = BASE_DIR / "data" / "display_layout.json"
DISPLAY_WIDGET_TYPES = {"live2d", "voice", "music", "video", "danmaku"}
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


def load_config() -> dict[str, Any]:
    """从模块独立配置文件组装运行时兼容配置。"""
    manager = ConfigManager()
    # 仅在新配置缺失时从旧 config.json 迁移普通字段，不复制秘密值。
    manager.migrate_legacy_config(LEGACY_CONFIG_PATH)
    app_config = manager.load("app", {"host": "127.0.0.1", "port": 8768, "debug": False})
    live_config = manager.load("live", {
        "room_id": 0, "reconnect": True, "reconnect_interval_seconds": 5,
        "connect_on_start": False,
    })
    live2d_config = manager.load("live2d", {"default_model": DEFAULT_MODEL})
    voice_config = manager.load("voice", {})
    obs_config = manager.load("obs", {
        "host": "127.0.0.1", "port": 4455, "timeout": 2.5,
        "password_file": "credentials/obs.json",
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


def _safe_model_dir(model_id: str) -> Path | None:
    if not isinstance(model_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", model_id):
        return None
    root = MODEL_ROOT.resolve()
    directory = (root / model_id).resolve()
    try:
        directory.relative_to(root)
    except ValueError:
        return None
    return directory if directory.is_dir() else None


def _model_config_path(directory: Path, model_id: str) -> Path | None:
    """查找模型配置：优先 model.json，其次 <模型目录名>.model.json。"""
    for filename in ("model.json", f"{model_id}.model.json"):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def _read_model_config(model_id: str) -> tuple[Path, dict[str, Any]]:
    directory = _safe_model_dir(model_id)
    if directory is None:
        raise FileNotFoundError(f"模型目录不存在：{model_id}")
    config_path = _model_config_path(directory, model_id)
    if config_path is None:
        raise FileNotFoundError(
            f"模型配置不存在：models/{model_id}/model.json 或 "
            f"models/{model_id}/{model_id}.model.json"
        )
    data = json.loads(config_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"{config_path.name} 必须是 JSON 对象")
    return directory, data


def _default_model_id() -> str:
    configured = CONFIG.get("live2d", {}).get("default_model", DEFAULT_MODEL)
    if isinstance(configured, str) and _safe_model_dir(configured):
        return configured
    return DEFAULT_MODEL


def _load_user_settings() -> dict[str, Any]:
    """从模块配置读取设置；首次运行时只迁移旧 user_settings.json 的普通字段。"""
    manager = ConfigManager()
    live = manager.load("live", {})
    live2d = manager.load("live2d", {})
    voice = manager.load("voice", {})
    legacy: dict[str, Any] = {}

    if USER_SETTINGS_PATH.is_file():
        try:
            raw = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                legacy = raw
        except Exception as exc:
            logger.warning("读取旧用户设置失败：%s", type(exc).__name__)

    changed_live = False
    changed_live2d = False
    changed_voice = False

    legacy_bilibili = legacy.get("bilibili") if isinstance(legacy.get("bilibili"), dict) else {}
    if not live.get("room_id") and legacy_bilibili.get("room_id"):
        try:
            live["room_id"] = max(0, int(legacy_bilibili["room_id"]))
            changed_live = True
        except (TypeError, ValueError):
            pass

    default_model = str(live2d.get("default_model") or DEFAULT_MODEL)
    if "model" not in live2d:
        live2d["model"] = legacy.get("model", default_model)
        changed_live2d = True
    if "models" not in live2d:
        live2d["models"] = legacy.get("models", {}) if isinstance(legacy.get("models"), dict) else {}
        changed_live2d = True
    elif not isinstance(live2d.get("models"), dict):
        live2d["models"] = {}
        changed_live2d = True
    if "idle_tips" not in live2d:
        live2d["idle_tips"] = legacy.get("idle_tips", {})
        changed_live2d = True

    if "tts" not in voice:
        legacy_tts = legacy.get("tts") if isinstance(legacy.get("tts"), dict) else {}
        voice["tts"] = legacy_tts
        changed_voice = True

    if changed_live:
        manager.save("live", live)
    if changed_live2d:
        manager.save("live2d", live2d)
    if changed_voice:
        manager.save("voice", voice)

    model_id = live2d.get("model")
    if not isinstance(model_id, str) or _safe_model_dir(model_id) is None:
        model_id = default_model
    models = live2d.get("models") if isinstance(live2d.get("models"), dict) else {}
    tts = voice.get("tts") if isinstance(voice.get("tts"), dict) else {}
    idle_tips = live2d.get("idle_tips") if isinstance(live2d.get("idle_tips"), dict) else {}
    try:
        room_id = max(0, int(live.get("room_id", CONFIG.get("bilibili", {}).get("room_id", 0))))
    except (TypeError, ValueError):
        room_id = 0
    return {
        "model": model_id,
        "models": models,
        "bilibili": {"room_id": room_id},
        "tts": tts,
        "idle_tips": idle_tips,
    }


def _model_preferences(settings: dict[str, Any], model_id: str) -> dict[str, Any]:
    raw = settings.get("models", {}).get(model_id, {})
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
        "scale": number("scale", 1.0, 0.25, 1.5),
        "x": number("x", 0.0, -1.0, 1.0),
        "y": number("y", 0.0, -1.0, 1.0),
        "bubble_x": number("bubble_x", 50.0, 0.0, 100.0),
        "bubble_bottom": number("bubble_bottom", 45.0, 0.0, 95.0),
        "bubble_width": integer("bubble_width", 420, 240, 760),
        "bubble_max_height": integer("bubble_max_height", 320, 100, 700),
        "bubble_max_items": integer("bubble_max_items", 5, 1, 12),
        "bubble_duration": integer("bubble_duration", 10, 2, 60),
        "bubble_font_size": integer("bubble_font_size", 20, 12, 40),
    }


def _save_user_settings(settings: dict[str, Any]) -> None:
    """把用户设置分别保存到 live/live2d/voice 模块配置，不再写根目录 JSON。"""
    manager = ConfigManager()
    live = manager.load("live", {})
    live2d = manager.load("live2d", {})
    voice = manager.load("voice", {})

    bilibili = settings.get("bilibili") if isinstance(settings.get("bilibili"), dict) else {}
    live["room_id"] = max(0, int(bilibili.get("room_id", live.get("room_id", 0)) or 0))

    model_id = settings.get("model")
    if isinstance(model_id, str) and _safe_model_dir(model_id):
        live2d["model"] = model_id
    models = settings.get("models")
    if isinstance(models, dict):
        live2d["models"] = models
    idle_tips = settings.get("idle_tips")
    if isinstance(idle_tips, dict):
        live2d["idle_tips"] = idle_tips
    tts = settings.get("tts")
    if isinstance(tts, dict):
        voice["tts"] = tts

    manager.save("live", live)
    manager.save("live2d", live2d)
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


@app.get("/api/idle-tips/settings")
def get_idle_tip_settings():
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    return jsonify({"ok": True, "preferences": _idle_tip_preferences(settings)})


@app.post("/api/idle-tips/settings")
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


@app.get("/api/tts/settings")
def get_tts_settings():
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    return jsonify({"ok": True, "preferences": _tts_preferences(settings), "voices": TTS_VOICES})


@app.post("/api/tts/settings")
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


@app.post("/api/tts/synthesize")
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
        return jsonify({"ok": True, "url": f"/api/tts/audio/{filename}"})
    except Exception as exc:
        logger.exception("Edge TTS 语音生成失败")
        return jsonify({"ok": False, "error": f"语音生成失败：{exc}"}), 502


@app.get("/api/tts/audio/<filename>")
def get_tts_audio(filename: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.mp3", filename):
        abort(404)
    if not TTS_CACHE_DIR.is_dir():
        abort(404)
    return send_from_directory(TTS_CACHE_DIR, filename, mimetype="audio/mpeg", max_age=0)


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


def _read_obs_password(password_file: str | None) -> str:
    """读取统一凭据库中的 OBS 密码；仅兼容旧版非 JSON 明文密码文件。"""
    if not password_file:
        return ""
    normalized = str(password_file).replace("\\", "/").strip("/")
    if normalized == "credentials/obs.json":
        try:
            secret = CredentialStore().load("obs") or {}
            return str(secret.get("password", "") or "")
        except Exception as exc:
            logger.warning("读取 OBS 凭据失败：%s", type(exc).__name__)
            return ""
    password_path = (BASE_DIR / str(password_file)).resolve()
    try:
        password_path.relative_to(BASE_DIR.resolve())
    except ValueError as exc:
        raise ValueError("OBS password_file 必须位于项目目录内。") from exc
    if not password_path.is_file():
        raise FileNotFoundError(f"OBS WebSocket 密码文件不存在：{password_file}")
    return password_path.read_text(encoding="utf-8-sig").strip()


def _refresh_obs_browser_sources() -> dict[str, Any]:
    """尝试通过 OBS WebSocket 刷新指向 /display 的浏览器源；失败时不影响设置保存。"""
    obs_config = CONFIG.get("obs_websocket", {})
    if not isinstance(obs_config, dict):
        obs_config = {}
    host = str(obs_config.get("host", "127.0.0.1")).strip() or "127.0.0.1"
    try:
        port = int(obs_config.get("port", 4455))
        timeout = max(0.5, min(5.0, float(obs_config.get("timeout", 2.5))))
    except (TypeError, ValueError):
        port, timeout = 4455, 2.5
    ws = None
    try:
        password_file = obs_config.get("password_file")
        password = _read_obs_password(password_file)
        if not password and not password_file:
            # 兼容旧版 config.json 中直接配置 password 的方式。
            password = str(obs_config.get("password", ""))
        ws = websocket.create_connection(
            f"ws://{host}:{port}",
            timeout=timeout,
            suppress_origin=True,
        )
        hello = json.loads(ws.recv())
        if hello.get("op") != 0:
            raise RuntimeError("OBS WebSocket 未返回 Hello 握手。")
        hello_data = hello.get("d", {})
        identify: dict[str, Any] = {"rpcVersion": 1, "eventSubscriptions": 0}
        authentication = hello_data.get("authentication")
        if authentication:
            if not password:
                raise RuntimeError("OBS WebSocket 已启用密码验证，但密码文件为空或未配置有效密码。")
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
            message = "已连接 OBS，但未找到 URL 指向 /display 的浏览器源，已跳过刷新。"
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



# OBS Live2D 来源自动控制。一个独立锁保证 watcher、设置刷新和退出清理不并发操作同一 WebSocket。
OBS_DISPLAY_LOCK = threading.RLock()
OBS_DISPLAY_STOP = threading.Event()
OBS_DISPLAY_SHOWN = False
OBS_DISPLAY_SHUTDOWN = False


def _open_obs_websocket(timeout_override: float | None = None):
    """连接并完成 OBS WebSocket v5 身份验证。"""
    obs_config = CONFIG.get("obs_websocket", {})
    if not isinstance(obs_config, dict):
        obs_config = {}
    host = str(obs_config.get("host", "127.0.0.1")).strip() or "127.0.0.1"
    try:
        port = int(obs_config.get("port", 4455))
        timeout = max(0.5, min(5.0, float(obs_config.get("timeout", 2.5))))
    except (TypeError, ValueError):
        port, timeout = 4455, 2.5

    password_file = obs_config.get("password_file")
    password = _read_obs_password(password_file)
    if not password and not password_file:
        password = str(obs_config.get("password", ""))

    if timeout_override is not None:
        timeout = max(0.3, min(timeout, float(timeout_override)))
    ws = websocket.create_connection(
        f"ws://{host}:{port}", timeout=timeout, suppress_origin=True
    )
    try:
        hello = json.loads(ws.recv())
        if hello.get("op") != 0:
            raise RuntimeError("OBS WebSocket 未返回 Hello 握手。")
        hello_data = hello.get("d", {})
        identify: dict[str, Any] = {"rpcVersion": 1, "eventSubscriptions": 0}
        authentication = hello_data.get("authentication")
        if authentication:
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


def _display_service_address() -> tuple[str, int]:
    """读取本程序 HTTP 服务的 IP 和端口，作为 OBS 浏览器源匹配基准。"""
    app_config = CONFIG.get("app", {})
    if not isinstance(app_config, dict):
        app_config = {}
    host = str(app_config.get("host", "127.0.0.1")).strip().lower() or "127.0.0.1"
    try:
        port = int(app_config.get("port", 8768))
    except (TypeError, ValueError):
        port = 8768
    return host, port


def _is_display_browser_url(value: Any) -> bool:
    """只匹配当前程序 IP、端口和 /display 路径。"""
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
        same_host = actual_host == expected_host or (
            actual_host in loopbacks and expected_host in loopbacks
        )
        return parsed.scheme.lower() in {"http", "https"} and same_host and (
            actual_port == expected_port and parsed.path.rstrip("/") == "/display"
        )
    except (TypeError, ValueError):
        return False


def _obs_set_display_sources_enabled(
    enabled: bool,
    refresh: bool = False,
    shutdown_mode: bool = False,
) -> dict[str, Any]:
    """在 OBS 所有场景（含嵌套组）中启用或隐藏全部 /display 浏览器源。"""
    with OBS_DISPLAY_LOCK:
        ws = None
        try:
            ws = _open_obs_websocket(timeout_override=1.0 if shutdown_mode else None)
            input_response = _obs_ws_request(ws, "GetInputList")
            matching_inputs: set[str] = set()
            for item in input_response.get("inputs", []):
                if item.get("inputKind") != "browser_source":
                    continue
                input_name = item.get("inputName")
                if not isinstance(input_name, str) or not input_name:
                    continue
                source_info = _obs_ws_request(ws, "GetInputSettings", {"inputName": input_name})
                settings = source_info.get("inputSettings") or {}
                if _is_display_browser_url(settings.get("url")):
                    matching_inputs.add(input_name)

            if not matching_inputs:
                return {
                    "ok": False,
                    "matched_inputs": 0,
                    "scene_items": 0,
                    "message": "未找到 URL 指向 /display 的浏览器源。",
                }

            if enabled and refresh:
                for input_name in sorted(matching_inputs):
                    _obs_ws_request(ws, "PressInputPropertiesButton", {
                        "inputName": input_name,
                        "propertyName": "refreshnocache",
                    })

            scene_response = _obs_ws_request(ws, "GetSceneList")
            scenes = scene_response.get("scenes", [])
            targets: list[tuple[str, int]] = []
            visited_groups: set[str] = set()

            def collect_items(container_name: str, items: list[dict[str, Any]]) -> None:
                for scene_item in items:
                    source_name = scene_item.get("sourceName")
                    scene_item_id = scene_item.get("sceneItemId")
                    if source_name in matching_inputs and isinstance(scene_item_id, int):
                        targets.append((container_name, scene_item_id))
                    if scene_item.get("isGroup") and isinstance(source_name, str):
                        group_key = source_name
                        if group_key in visited_groups:
                            continue
                        visited_groups.add(group_key)
                        group_response = _obs_ws_request(
                            ws, "GetGroupSceneItemList", {"sceneName": group_key}
                        )
                        collect_items(group_key, group_response.get("sceneItems", []))

            for scene in scenes:
                scene_name = scene.get("sceneName")
                if not isinstance(scene_name, str) or not scene_name:
                    continue
                scene_items = _obs_ws_request(
                    ws, "GetSceneItemList", {"sceneName": scene_name}
                ).get("sceneItems", [])
                collect_items(scene_name, scene_items)

            changed = 0
            for scene_name, scene_item_id in targets:
                _obs_ws_request(ws, "SetSceneItemEnabled", {
                    "sceneName": scene_name,
                    "sceneItemId": scene_item_id,
                    "sceneItemEnabled": bool(enabled),
                })
                changed += 1

            action = "显示" if enabled else "隐藏"
            message = (
                f"已{action} {changed} 个 /display 场景项，"
                f"匹配到 {len(matching_inputs)} 个浏览器源。"
            )
            logger.info("OBS Live2D 自动控制：%s", message)
            return {
                "ok": True,
                "matched_inputs": len(matching_inputs),
                "scene_items": changed,
                "message": message,
            }
        except Exception as exc:
            message = f"OBS Live2D 来源{('显示' if enabled else '隐藏')}失败：{exc}"
            logger.warning("%s", message)
            return {"ok": False, "matched_inputs": 0, "scene_items": 0, "message": message}
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass


def _obs_live2d_watchdog() -> None:
    """B 站 WebSocket 真实连接成功后，刷新并显示所有匹配来源；失败则每 3 秒重试。"""
    global OBS_DISPLAY_SHOWN
    while not OBS_DISPLAY_STOP.wait(3.0):
        with BILIBILI_STATE_LOCK:
            bilibili_connected = BILIBILI_STATE.get("connection_state") == "connected"
        # 不等待 /display 报告模型加载：展示页由 OBS 浏览器源自己加载。
        if not bilibili_connected:
            continue
        if OBS_DISPLAY_SHOWN:
            continue
        result = _obs_set_display_sources_enabled(True, refresh=True)
        if result.get("ok") and result.get("matched_inputs", 0) > 0:
            OBS_DISPLAY_SHOWN = True
        # OBS 未启动或尚未添加匹配源时不置位，下一轮继续重试。


def _shutdown_obs_live2d_sources() -> None:
    """程序退出或 Windows 关闭 CMD 时，隐藏所有匹配的 OBS 浏览器源。"""
    global OBS_DISPLAY_SHUTDOWN
    with OBS_DISPLAY_LOCK:
        if OBS_DISPLAY_SHUTDOWN:
            return
        OBS_DISPLAY_STOP.set()
        result = _obs_set_display_sources_enabled(
            False, refresh=False, shutdown_mode=True
        )
        if result.get("ok"):
            OBS_DISPLAY_SHUTDOWN = True
        else:
            # 清理失败时允许 atexit/finally 再尝试一次。
            logger.warning("程序退出时未能隐藏全部匹配的 OBS 浏览器源：%s", result.get("message"))


# CMD 窗口关闭事件（右上角 X）需要独立于 atexit/finally 处理。
OBS_CONSOLE_HANDLER = None


def _handle_windows_console_event(event_type: int) -> bool:
    if event_type not in {2, 5, 6}:  # CTRL_CLOSE_EVENT / CTRL_LOGOFF_EVENT / CTRL_SHUTDOWN_EVENT
        return False
    try:
        logger.info("检测到 Windows 控制台关闭事件，正在隐藏 OBS Live2D 来源……")
        _shutdown_obs_live2d_sources()
    except Exception:
        pass
    return True


def _register_windows_console_handler() -> None:
    global OBS_CONSOLE_HANDLER
    if os.name != "nt":
        return
    try:
        import ctypes
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
        OBS_CONSOLE_HANDLER = handler_type(_handle_windows_console_event)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.SetConsoleCtrlHandler(OBS_CONSOLE_HANDLER, True):
            error_code = ctypes.get_last_error()
            OBS_CONSOLE_HANDLER = None
            raise OSError(error_code, "SetConsoleCtrlHandler 注册失败")
        logger.info("已注册 Windows CMD 关闭事件处理。")
    except Exception as exc:
        logger.warning("注册 Windows CMD 关闭事件处理失败：%s", exc)


def _available_models() -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not MODEL_ROOT.is_dir():
        return result
    for directory in sorted(MODEL_ROOT.iterdir(), key=lambda item: item.name.casefold()):
        if not directory.is_dir() or _safe_model_dir(directory.name) is None:
            continue
        config_path = _model_config_path(directory, directory.name)
        if config_path is None:
            continue
        try:
            data = json.loads(config_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict) or not isinstance(data.get("model"), str):
                continue
            if not isinstance(data.get("textures"), list) or not data["textures"]:
                continue
            result.append({"id": directory.name, "name": str(data.get("name") or directory.name)})
        except Exception:
            logger.warning("跳过无效模型配置：%s", config_path)
    return result


def _model_assets(model_id: str) -> tuple[bool, list[str], dict[str, Any]]:
    directory = _safe_model_dir(model_id)
    if directory is None:
        return False, [f"模型目录不存在：{model_id}"], {}
    try:
        _, data = _read_model_config(model_id)
    except Exception as exc:
        return False, [str(exc)], {}

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
                references.extend(item["file"] for item in group if isinstance(item, dict) and isinstance(item.get("file"), str))

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


def model_status() -> dict[str, Any]:
    settings = _load_user_settings()
    model_id = settings["model"]
    directory = _safe_model_dir(model_id)
    model_config = _model_config_path(directory, model_id) if directory else None
    runtime_file = BASE_DIR / "static" / "js" / "live2d.min.js"
    assets_ok, missing, _ = _model_assets(model_id)
    with RUNTIME_STATE_LOCK:
        runtime_state = dict(RUNTIME_STATE)
    return {
        "format": CONFIG.get("live2d", {}).get("format", "MOC2"),
        "name": model_id,
        "directory_exists": bool(directory),
        "config_exists": bool(model_config and model_config.is_file()),
        "runtime_file_exists": runtime_file.is_file(),
        "assets_ready": assets_ok,
        "missing_assets": missing,
        "runtime_state": runtime_state["state"],
        "runtime_ready": runtime_state["state"] == "loaded",
        "message": runtime_state["message"],
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
        if not valid and BILIBILI_STATE["connection_state"] in {"connected", "connecting", "reconnecting"}:
            should_stop = True
            BILIBILI_STATE["connected"] = False
            BILIBILI_STATE["connection_state"] = "disconnected"
    if should_stop:
        LIVE_CLIENT.stop()
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
APPLICATION_RUNTIME.register_module("live2d", LIVE2D_MODULE, initial_state="stopped", auto_start_on_connect=True)
APPLICATION_RUNTIME.register_module("voice", VOICE_MODULE, initial_state="stopped", auto_start_on_connect=True)


MUSIC_MODULE = MusicModuleFacade(
    event_bus=APPLICATION_RUNTIME.events,
    config=ConfigManager().load("music", {}),
    cookie_provider=_music_cookie_provider,
    room_id=int(_load_user_settings()["bilibili"]["room_id"]),
)
APPLICATION_RUNTIME.register_module("music", MUSIC_MODULE)

VIDEO_MODULE = VideoModuleFacade(
    event_bus=APPLICATION_RUNTIME.events,
    config=ConfigManager().load("video", {}),
    cookie_provider=_music_cookie_provider,
    room_id=int(_load_user_settings()["bilibili"]["room_id"]),
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
    """启动时检查登录状态并恢复房间号显示，但必须由用户手动发起连接。"""
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    room_id = int(settings["bilibili"]["room_id"])
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["room_id"] = room_id
    valid, message, _ = _login_status()
    logger.info(
        "启动检查完成：登录有效=%s，已保存房间号=%s；不会在程序启动时自动连接直播间。%s",
        valid, room_id, message,
    )


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/display")
def display():
    return render_template("display.html")


@app.get("/live2d")
def live2d_settings_page():
    return redirect("/#live2d-settings")


@app.get("/dashboard")
def dashboard_page():
    return render_template("combined_display.html", display_mode=False)


@app.get("/voice")
def voice_settings_page():
    return render_template("voice.html")


@app.get("/settings")
def system_settings_page():
    return render_template("settings.html")


@app.get("/credentials")
def credentials_page():
    return render_template("credentials.html")


@app.get("/display/combined")
def combined_display_page():
    return render_template("combined_display.html", display_mode=True)


@app.get("/music")
def music_control_page():
    return render_template("music_queue.html")


@app.get("/music/settings")
def music_settings_page():
    return render_template("music_settings.html")


@app.get("/music/display")
def music_display_page():
    return render_template("music_display.html")


@app.get("/video")
def video_control_page():
    return render_template("video_queue.html")


@app.get("/video/settings")
def video_settings_page():
    return render_template("video_settings.html")


@app.get("/video/display")
def video_display_page():
    return render_template("video_display.html")


@app.get("/api/display/settings")
def get_display_settings():
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    model_id = settings["model"]
    available = _available_models()
    requested_model = request.args.get("model")
    if isinstance(requested_model, str) and any(item["id"] == requested_model for item in available):
        model_id = requested_model
    if not any(item["id"] == model_id for item in available):
        model_id = _default_model_id()
    return jsonify({
        "ok": True,
        "model": model_id,
        "models": available,
        "preferences": _model_preferences(settings, model_id),
    })


@app.post("/api/display/settings")
def save_display_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    model_id = payload.get("model")
    if not isinstance(model_id, str) or not any(item["id"] == model_id for item in _available_models()):
        return jsonify({"ok": False, "error": "所选模型不存在或缺少有效模型配置。"}), 400

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
            "scale": bounded_number("scale", 1.0, 0.25, 1.5),
            "x": bounded_number("x", 0.0, -1.0, 1.0),
            "y": bounded_number("y", 0.0, -1.0, 1.0),
            "bubble_x": bounded_number("bubble_x", 50.0, 0.0, 100.0),
            "bubble_bottom": bounded_number("bubble_bottom", 45.0, 0.0, 95.0),
            "bubble_width": bounded_integer("bubble_width", 420, 240, 760),
            "bubble_max_height": bounded_integer("bubble_max_height", 320, 100, 700),
            "bubble_max_items": bounded_integer("bubble_max_items", 5, 1, 12),
            "bubble_duration": bounded_integer("bubble_duration", 10, 2, 60),
            "bubble_font_size": bounded_integer("bubble_font_size", 20, 12, 40),
        }
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with SETTINGS_LOCK:
        settings = _load_user_settings()
        settings["model"] = model_id
        settings.setdefault("models", {})[model_id] = preferences
        _save_user_settings(settings)

    logger.info("已保存 Live2D 展示设置：model=%s bubble_x=%.1f bubble_bottom=%.1f", model_id, preferences["bubble_x"], preferences["bubble_bottom"])
    if payload.get("refresh_obs", True) is False:
        obs_refresh = {"message": "本次保存未刷新 OBS 浏览器源。"}
    else:
        with OBS_DISPLAY_LOCK:
            obs_refresh = _refresh_obs_browser_sources()
    return jsonify({
        "ok": True,
        "model": model_id,
        "preferences": preferences,
        "obs_refresh": obs_refresh,
    })


@app.get("/models/<model_id>/model.json")
def model_config_file(model_id: str):
    try:
        directory, data = _read_model_config(model_id)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        abort(404)
    with SETTINGS_LOCK:
        settings = _load_user_settings()
    preferences = _model_preferences(settings, model_id)
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
    return jsonify(data)


@app.get("/models/<path:filename>")
def model_asset(filename: str):
    if not MODEL_ROOT.is_dir():
        abort(404)
    return send_from_directory(MODEL_ROOT, filename, conditional=True)


@app.post("/api/live2d/status")
def update_live2d_status():
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
    # 新版统一保存到普通直播配置；同时更新旧用户设置，兼容现有页面逻辑。
    config_manager = ConfigManager()
    live_config = config_manager.load("live", {})
    live_config["room_id"] = room_id
    config_manager.save("live", live_config)
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
    LIVE_CLIENT.stop()
    return jsonify({"ok": True, "message": "已请求断开直播连接。"})





@app.get("/api/modules")
def api_modules():
    return jsonify({"ok": True, "modules": APPLICATION_RUNTIME.modules.list_status()})


@app.post("/api/modules/<module_name>/<action>")
def api_module_action(module_name: str, action: str):
    if action not in {"start", "pause", "resume", "close"}:
        return jsonify({"ok": False, "error": "不支持的模块操作。"}), 400
    if module_name not in APPLICATION_RUNTIME.modules.list_status():
        return jsonify({"ok": False, "error": "模块不存在或尚未迁移。"}), 404
    if action in {"start", "resume"}:
        with BILIBILI_STATE_LOCK:
            connected = BILIBILI_STATE["connected"]
        if not connected:
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
        return jsonify({"ok": False, "error": f"模块操作失败：{type(exc).__name__}"}), 500


@app.get("/api/music/queue")
def api_music_queue():
    try:
        snapshot = APPLICATION_RUNTIME.async_runtime.submit(MUSIC_MODULE.queue_snapshot()).result(timeout=5)
        return jsonify(snapshot)
    except Exception as exc:
        logger.exception("读取歌曲队列快照失败。")
        return jsonify({"ok": False, "error": f"读取歌曲队列失败：{type(exc).__name__}"}), 503


@app.get("/api/video/queue")
def api_video_queue():
    try:
        snapshot = APPLICATION_RUNTIME.async_runtime.submit(VIDEO_MODULE.queue_snapshot()).result(timeout=5)
        return jsonify(snapshot)
    except Exception as exc:
        logger.exception("读取视频队列快照失败。")
        return jsonify({"ok": False, "error": f"读取视频队列失败：{type(exc).__name__}"}), 503


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
        return jsonify({"ok": False, "message": f"操作失败：{type(exc).__name__}"}), 500


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
        return jsonify({"ok": False, "message": f"操作失败：{type(exc).__name__}"}), 500



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
        widgets.append({
            "id": widget_type, "type": widget_type,
            "x": round(x, 2), "y": round(y, 2),
            "w": round(w, 2), "h": round(h, 2),
        })
        used_types.add(widget_type)
    return {"version": 1, "widgets": widgets}


@app.get("/api/settings/music")
def get_music_settings():
    config = ConfigManager().load("music", {})
    lx = config.get("lx_music", {})
    search = config.get("search", {})
    requests_config = config.get("requests", {})
    return jsonify({
        "ok": True,
        "lx_music": {
            "base_url": lx.get("base_url", "http://127.0.0.1:23330"),
            "timeout_seconds": lx.get("timeout_seconds", 5),
        },
        "search": {"max_results_per_platform": search.get("max_results_per_platform", 20)},
        "requests": {
            "cooldown_seconds": requests_config.get("cooldown_seconds", 10),
            "queue_limit": requests_config.get("queue_limit", 100),
            "require_fans_medal": requests_config.get("require_fans_medal", 0),
        },
        "module_state": APPLICATION_RUNTIME.modules.list_status().get("music", {}).get("state", "stopped"),
    })


@app.post("/api/settings/music")
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
        base_url = str(payload.get("base_url", lx.get("base_url", ""))).strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("洛雪 API 地址必须以 http:// 或 https:// 开头。")
        timeout = int(payload.get("timeout_seconds", lx.get("timeout_seconds", 5)))
        max_results = int(payload.get("max_results_per_platform", search.get("max_results_per_platform", 20)))
        cooldown = int(payload.get("cooldown_seconds", req.get("cooldown_seconds", 10)))
        queue_limit = int(payload.get("queue_limit", req.get("queue_limit", 100)))
        require_medal = int(payload.get("require_fans_medal", req.get("require_fans_medal", 0)))
        if not 2 <= timeout <= 30 or not 1 <= max_results <= 50:
            raise ValueError("接口超时应为 2–30 秒，单平台搜索数量应为 1–50。")
        if not 0 <= cooldown <= 3600 or not 1 <= queue_limit <= 1000 or require_medal not in {0, 1}:
            raise ValueError("冷却时间、队列上限或粉丝牌选项无效。")
        lx.update({"base_url": base_url, "timeout_seconds": timeout})
        search["max_results_per_platform"] = max_results
        req.update({
            "cooldown_seconds": cooldown,
            "queue_limit": queue_limit,
            "require_fans_medal": require_medal,
        })
        ConfigManager().save("music", config)
        MUSIC_MODULE.reload_config(config)
        return jsonify({"ok": True, "message": "歌曲模块配置已保存。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "歌曲配置保存失败。"}), 400


@app.get("/api/settings/video")
def get_video_settings():
    config = ConfigManager().load("video", {})
    chrome = config.get("chrome", {})
    req = config.get("requests", {})
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
            "require_fan_medal": bool(req.get("require_fan_medal", True)),
        },
        "module_state": APPLICATION_RUNTIME.modules.list_status().get("video", {}).get("state", "stopped"),
    })


@app.post("/api/settings/video")
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
        binary_path = str(payload.get("binary_path", chrome.get("binary_path", ""))).strip()
        profile_dir = str(payload.get("profile_dir", chrome.get("profile_dir", "data/video/chrome_profile"))).strip()
        if "\n" in binary_path or "\r" in binary_path or not profile_dir or "\n" in profile_dir or "\r" in profile_dir:
            raise ValueError("Chrome 路径或 Profile 目录无效。")
        timeout = int(payload.get("page_load_timeout_seconds", chrome.get("page_load_timeout_seconds", 45)))
        cooldown = int(payload.get("cooldown_seconds", req.get("cooldown_seconds", 60)))
        queue_limit = int(payload.get("queue_limit", req.get("queue_limit", 30)))
        max_duration = int(payload.get("max_video_duration_seconds", req.get("max_video_duration_seconds", 300)))
        require_medal = bool(payload.get("require_fan_medal", req.get("require_fan_medal", True)))
        if not 10 <= timeout <= 180 or not 0 <= cooldown <= 3600:
            raise ValueError("页面加载超时应为 10–180 秒，冷却时间应为 0–3600 秒。")
        if not 1 <= queue_limit <= 1000 or not 1 <= max_duration <= 3600:
            raise ValueError("视频队列上限或视频时长限制无效。")
        chrome.update({
            "binary_path": binary_path,
            "profile_dir": profile_dir,
            "page_load_timeout_seconds": timeout,
        })
        req.update({
            "cooldown_seconds": cooldown,
            "queue_limit": queue_limit,
            "max_video_duration_seconds": max_duration,
            "require_fan_medal": require_medal,
        })
        ConfigManager().save("video", config)
        VIDEO_MODULE.reload_config(config)
        return jsonify({"ok": True, "message": "视频模块配置已保存。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "视频配置保存失败。"}), 400


def _legacy_obs_credential_candidates() -> dict[str, Any]:
    """只探测旧凭据是否存在；绝不向前端返回密码或 Cookie 内容。"""
    found = {"inline_password": False, "password_file": None}
    try:
        if LEGACY_CONFIG_PATH.is_file():
            legacy = json.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8-sig"))
            obs = legacy.get("obs_websocket", {}) if isinstance(legacy, dict) else {}
            if isinstance(obs, dict):
                found["inline_password"] = bool(str(obs.get("password", "") or ""))
                old_file = str(obs.get("password_file", "") or "").strip()
                if old_file and old_file.replace(chr(92), "/") != "credentials/obs.json":
                    candidate = (BASE_DIR / old_file).resolve()
                    try:
                        candidate.relative_to(BASE_DIR.resolve())
                        if candidate.is_file():
                            found["password_file"] = str(candidate.relative_to(BASE_DIR))
                    except ValueError:
                        pass
    except Exception as exc:
        logger.warning("检查旧 OBS 凭据失败：%s", type(exc).__name__)
    return found


@app.get("/api/credentials/status")
def get_credentials_status():
    valid, message, _ = _login_status()
    obs_status = CredentialStore().status("obs")
    legacy = _legacy_obs_credential_candidates()
    return jsonify({
        "ok": True,
        "bilibili": {"configured": bool(valid), "message": message},
        "obs": {"configured": bool(obs_status.get("configured"))},
        "legacy": {
            "obs_inline_password": bool(legacy["inline_password"]),
            "obs_password_file": legacy["password_file"],
            "migration_available": bool(legacy["inline_password"] or legacy["password_file"]),
        },
    })


@app.post("/api/credentials/migrate")
def migrate_legacy_credentials():
    """显式迁移旧 OBS 密码；成功后清理旧配置中的明文值。"""
    store = CredentialStore()
    if store.status("obs").get("configured"):
        return jsonify({"ok": False, "error": "OBS 凭据库中已有密码；为避免覆盖，未执行迁移。"}), 409
    password = ""
    password_file_path = None
    try:
        legacy_data = {}
        if LEGACY_CONFIG_PATH.is_file():
            legacy_data = json.loads(LEGACY_CONFIG_PATH.read_text(encoding="utf-8-sig"))
            if not isinstance(legacy_data, dict):
                legacy_data = {}
        obs = legacy_data.get("obs_websocket", {})
        if not isinstance(obs, dict):
            obs = {}
        password = str(obs.get("password", "") or "")
        old_file = str(obs.get("password_file", "") or "").strip()
        if not password and old_file and old_file.replace(chr(92), "/") != "credentials/obs.json":
            candidate = (BASE_DIR / old_file).resolve()
            candidate.relative_to(BASE_DIR.resolve())
            if candidate.is_file():
                password_file_path = candidate
                password = candidate.read_text(encoding="utf-8-sig").strip()
        if not password:
            return jsonify({"ok": False, "error": "未发现可自动迁移的旧 OBS 明文密码。"}), 404

        store.save("obs", {"password": password})
        password = ""
        if isinstance(legacy_data.get("obs_websocket"), dict):
            clean_obs = dict(legacy_data["obs_websocket"])
            clean_obs.pop("password", None)
            clean_obs["password_file"] = "credentials/obs.json"
            legacy_data["obs_websocket"] = clean_obs
            temporary = LEGACY_CONFIG_PATH.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(legacy_data, ensure_ascii=False, indent=2) + chr(10), encoding="utf-8")
            os.replace(temporary, LEGACY_CONFIG_PATH)
        if password_file_path and password_file_path.exists():
            password_file_path.unlink()
        VIDEO_MODULE.reload_obs_settings()
        _refresh_obs_browser_sources()
        return jsonify({"ok": True, "message": "旧 OBS 密码已迁移到本机凭据库，并清理旧明文配置。"})
    except Exception as exc:
        logger.warning("迁移旧 OBS 凭据失败：%s", type(exc).__name__)
        return jsonify({"ok": False, "error": "旧 OBS 凭据迁移失败；请检查旧配置文件权限和格式。"}), 500
    finally:
        password = ""


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
                  "source_name": source_name, "password_file": "credentials/obs.json"}
        ConfigManager().save("obs", config)
        CONFIG["obs_websocket"] = {
            "host": host, "port": port, "timeout": timeout,
            "password_file": "credentials/obs.json",
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
        "level": config.get("level", "INFO"),
        "retention_days": int(config.get("retention_days", 7)),
        "retention_options": config.get("retention_options", [3, 5, 7, 15, 30, 60, 90, 0]),
    })


@app.post("/api/settings/logging")
def save_logging_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "请求内容必须是 JSON 对象。"}), 400
    try:
        level = str(payload.get("level", "INFO")).upper()
        retention = int(payload.get("retention_days", 7))
        allowed_retention = {3, 5, 7, 15, 30, 60, 90, 0}
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("日志等级无效。")
        if retention not in allowed_retention:
            raise ValueError("日志保留天数无效。")
        config = ConfigManager().load("logging", {})
        config.update({
            "level": level,
            "retention_days": retention,
            "retention_options": [3, 5, 7, 15, 30, 60, 90, 0],
            "daily_rotation": True,
        })
        ConfigManager().save("logging", config)
        setup_logging()
        logger.info("日志设置已更新：level=%s retention_days=%s", level, retention)
        return jsonify({"ok": True, "message": "日志设置已保存并立即生效。"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc) or "日志设置保存失败。"}), 400


@app.get("/api/display/layout/default")
def get_default_combined_display_layout():
    return jsonify({"ok": True, "layout": _default_display_layout()})


@app.get("/api/display/layout")
def get_combined_display_layout():
    try:
        if DISPLAY_LAYOUT_PATH.is_file():
            payload = json.loads(DISPLAY_LAYOUT_PATH.read_text(encoding="utf-8"))
            layout = _sanitize_display_layout(payload)
        else:
            layout = _default_display_layout()
        return jsonify({"ok": True, "layout": layout})
    except Exception as exc:
        logger.warning("读取综合展示布局失败：%s", type(exc).__name__)
        return jsonify({"ok": True, "layout": _default_display_layout()})


@app.post("/api/display/layout")
def save_combined_display_layout():
    payload = request.get_json(silent=True)
    try:
        layout = _sanitize_display_layout(payload)
        DISPLAY_LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = DISPLAY_LAYOUT_PATH.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, DISPLAY_LAYOUT_PATH)
        return jsonify({"ok": True, "layout": layout, "message": "综合展示布局已保存。"})
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
            "moc2_runtime": live2d["runtime_ready"],
            "music": True,
            "video": True,
        },
        "modules": APPLICATION_RUNTIME.modules.list_status(),
    })


def _initialize_login_state() -> None:
    valid, message, _ = _login_status()
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["message"] = message


def _credential_watchdog() -> None:
    while True:
        time.sleep(60)
        with BILIBILI_STATE_LOCK:
            should_check = BILIBILI_STATE["connection_state"] in {"connected", "connecting", "reconnecting"}
        if should_check:
            valid, message, _ = _login_status()
            if not valid:
                logger.warning("B站登录凭据失效，已停止直播连接：%s", message)


_initialize_login_state()


if __name__ == "__main__":
    host = CONFIG["app"].get("host", "127.0.0.1")
    port = int(CONFIG["app"].get("port", 8768))
    debug = bool(CONFIG["app"].get("debug", False))
    APPLICATION_RUNTIME.start()
    threading.Thread(target=_credential_watchdog, name="bili-credential-watchdog", daemon=True).start()
    atexit.register(_shutdown_obs_live2d_sources)
    atexit.register(APPLICATION_RUNTIME.shutdown)
    _register_windows_console_handler()
    _restore_saved_live_connection()
    threading.Thread(
        target=_obs_live2d_watchdog,
        name="obs-live2d-watchdog",
        daemon=True,
    ).start()
    logger.info("Starting local management server at http://%s:%s", host, port)
    try:
        app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)
    finally:
        # 先停止唯一的 B 站 WebSocket 连接，再统一关闭业务模块和异步运行时。
        LIVE_CLIENT.stop()
        _shutdown_obs_live2d_sources()
        APPLICATION_RUNTIME.shutdown()
