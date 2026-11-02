# -*- coding: utf-8 -*-
"""Bili Live Danmaku application entry point."""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
MODEL_ROOT = BASE_DIR / "models"
USER_SETTINGS_PATH = BASE_DIR / "user_settings.json"
DEFAULT_MODEL = "live_uu"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("bili_live_danmaku")

RUNTIME_STATE_LOCK = threading.Lock()
SETTINGS_LOCK = threading.Lock()
RUNTIME_STATE: dict[str, str] = {
    "state": "not_started",
    "message": "OBS display has not initialized the Live2D runtime.",
}


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
        return {"model": default_model, "models": {}}
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
        return {"model": model_id, "models": models}
    except Exception as exc:
        logger.warning("读取用户展示设置失败，将使用默认设置：%s", exc)
        return {"model": default_model, "models": {}}


def _model_preferences(settings: dict[str, Any], model_id: str) -> dict[str, float]:
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

    return {
        "scale": number("scale", 1.0, 0.5, 1.8),
        "x": number("x", 0.0, -1.0, 1.0),
        "y": number("y", 0.0, -1.0, 1.0),
    }


def _save_user_settings(settings: dict[str, Any]) -> None:
    USER_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = USER_SETTINGS_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, USER_SETTINGS_PATH)


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
    for key in ("textures",):
        values = data.get(key, [])
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
        return jsonify({"ok": False, "error": "所选模型不存在或缺少有效的 model.json。"}), 400

    def bounded_number(key: str, default: float, low: float, high: float) -> float:
        try:
            value = float(payload.get(key, default))
            if not math.isfinite(value):
                raise ValueError
            return max(low, min(high, value))
        except (TypeError, ValueError):
            raise ValueError(f"{key} 参数无效。")

    try:
        preferences = {
            "scale": bounded_number("scale", 1.0, 0.5, 1.8),
            "x": bounded_number("x", 0.0, -1.0, 1.0),
            "y": bounded_number("y", 0.0, -1.0, 1.0),
        }
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    with SETTINGS_LOCK:
        settings = _load_user_settings()
        settings["model"] = model_id
        settings.setdefault("models", {})[model_id] = preferences
        _save_user_settings(settings)

    logger.info("已保存 Live2D 展示设置：model=%s scale=%.2f x=%.2f y=%.2f", model_id, preferences["scale"], preferences["x"], preferences["y"])
    return jsonify({"ok": True, "model": model_id, "preferences": preferences})


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

    # Apply transform through Cubism 2's native model layout matrix, not CSS
    # scaling of the canvas. Preserve each model's own original layout values.
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
    if state not in {"loading", "loaded", "error"}:
        return jsonify({"ok": False, "error": "Invalid Live2D state."}), 400
    if not isinstance(message, str):
        message = str(message)
    with RUNTIME_STATE_LOCK:
        RUNTIME_STATE["state"] = state
        RUNTIME_STATE["message"] = message[:500]
    logger.info("Live2D runtime state: %s - %s", state, message[:200])
    return jsonify({"ok": True})


@app.get("/api/status")
def api_status():
    live2d = model_status()
    return jsonify({
        "application": "Bili Live Danmaku",
        "web_server": "running",
        "bilibili": {
            "logged_in": False,
            "connected": False,
            "message": "Bilibili QR login and live connection are not implemented yet.",
        },
        "live2d": live2d,
        "features": {
            "danmaku": False,
            "gift_events": False,
            "tts": False,
            "mouth_sync": False,
            "moc2_runtime": live2d["runtime_ready"],
        },
    })


if __name__ == "__main__":
    host = CONFIG["app"].get("host", "127.0.0.1")
    port = int(CONFIG["app"].get("port", 8768))
    debug = bool(CONFIG["app"].get("debug", False))
    logger.info("Starting local management server at http://%s:%s", host, port)
    app.run(host=host, port=port, debug=debug, threaded=True)
