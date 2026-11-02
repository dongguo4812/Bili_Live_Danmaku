# -*- coding: utf-8 -*-
"""Bili Live Danmaku application entry point."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from urllib.parse import urlparse

import websocket
from collections import deque
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from bilibili_client import BilibiliClientError, LiveRoomClient, QRLogin, validate_cookie_file

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
MODEL_ROOT = BASE_DIR / "models"
USER_SETTINGS_PATH = BASE_DIR / "user_settings.json"
DEFAULT_MODEL = "live_uu"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bili_live_danmaku")

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
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("config.json must contain a JSON object")
    return data


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
    on_event=lambda item: _append_danmaku(item),
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
    default_model = _default_model_id()
    if not USER_SETTINGS_PATH.is_file():
        return {"model": default_model, "models": {}, "bilibili": {"room_id": 0}}
    try:
        data = json.loads(USER_SETTINGS_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("设置文件格式错误")
        model_id = data.get("model")
        if not isinstance(model_id, str) or _safe_model_dir(model_id) is None:
            model_id = default_model
        models = data.get("models")
        if not isinstance(models, dict):
            models = {}
        bilibili = data.get("bilibili")
        if not isinstance(bilibili, dict):
            bilibili = {}
        try:
            room_id = max(0, int(bilibili.get("room_id", 0)))
        except (TypeError, ValueError):
            room_id = 0
        return {"model": model_id, "models": models, "bilibili": {"room_id": room_id}}
    except Exception as exc:
        logger.warning("读取用户设置失败，将使用默认设置：%s", exc)
        return {"model": default_model, "models": {}, "bilibili": {"room_id": 0}}


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
        "scale": number("scale", 1.0, 0.5, 1.8),
        "x": number("x", 0.0, -1.0, 1.0),
        "y": number("y", 0.0, -1.0, 1.0),
        "bubble_x": number("bubble_x", 50.0, 0.0, 100.0),
        "bubble_bottom": number("bubble_bottom", 45.0, 0.0, 95.0),
        "bubble_width": integer("bubble_width", 420, 240, 760),
        "bubble_max_height": integer("bubble_max_height", 320, 100, 700),
        "bubble_max_items": integer("bubble_max_items", 5, 1, 12),
        "bubble_duration": integer("bubble_duration", 10, 2, 60),
    }


def _save_user_settings(settings: dict[str, Any]) -> None:
    USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = USER_SETTINGS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, USER_SETTINGS_PATH)



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
    password = str(obs_config.get("password", ""))
    ws = None
    try:
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
                raise RuntimeError("OBS WebSocket 已启用密码验证，但 config.json 未配置密码。")
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
            settings = item.get("inputSettings") or {}
            source_url = settings.get("url", "")
            if not isinstance(input_name, str) or not isinstance(source_url, str):
                continue
            parsed = urlparse(source_url)
            if parsed.path.rstrip("/") != "/display":
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


def _set_bilibili_connection_state(state: str, message: str) -> None:
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["connection_state"] = state
        BILIBILI_STATE["connected"] = state == "connected"
        BILIBILI_STATE["message"] = message
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
    """程序启动时恢复有效登录，并自动连接已保存的直播间。"""
    connected, message = _connect_saved_room("程序启动恢复")
    if connected:
        logger.info("启动恢复：%s", message)
    else:
        logger.info("启动恢复未连接直播间：%s", message)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/display")
def display():
    return render_template("display.html")


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
            "scale": bounded_number("scale", 1.0, 0.5, 1.8),
            "x": bounded_number("x", 0.0, -1.0, 1.0),
            "y": bounded_number("y", 0.0, -1.0, 1.0),
            "bubble_x": bounded_number("bubble_x", 50.0, 0.0, 100.0),
            "bubble_bottom": bounded_number("bubble_bottom", 45.0, 0.0, 95.0),
            "bubble_width": bounded_integer("bubble_width", 420, 240, 760),
            "bubble_max_height": bounded_integer("bubble_max_height", 320, 100, 700),
            "bubble_max_items": bounded_integer("bubble_max_items", 5, 1, 12),
            "bubble_duration": bounded_integer("bubble_duration", 10, 2, 60),
        }
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with SETTINGS_LOCK:
        settings = _load_user_settings()
        settings["model"] = model_id
        settings.setdefault("models", {})[model_id] = preferences
        _save_user_settings(settings)

    logger.info("已保存 Live2D 展示设置：model=%s bubble_x=%.1f bubble_bottom=%.1f", model_id, preferences["bubble_x"], preferences["bubble_bottom"])
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
    with SETTINGS_LOCK:
        settings = _load_user_settings()
        settings["bilibili"]["room_id"] = room_id
        _save_user_settings(settings)
    with BILIBILI_STATE_LOCK:
        BILIBILI_STATE["room_id"] = room_id
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
            "tts": False,
            "mouth_sync": False,
            "moc2_runtime": live2d["runtime_ready"],
        },
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
    threading.Thread(target=_credential_watchdog, name="bili-credential-watchdog", daemon=True).start()
    _restore_saved_live_connection()
    logger.info("Starting local management server at http://%s:%s", host, port)
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)
