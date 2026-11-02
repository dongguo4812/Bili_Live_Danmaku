# -*- coding: utf-8 -*-
"""Bili Live Danmaku application entry point."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
MODEL_ROOT = BASE_DIR / "models"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bili_live_danmaku")

RUNTIME_STATE_LOCK = threading.Lock()
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
app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)


def _model_assets() -> tuple[bool, list[str], dict[str, Any]]:
    live2d = CONFIG["live2d"]
    model_dir = (BASE_DIR / live2d["model_dir"]).resolve()
    model_config = (BASE_DIR / live2d["model_config"]).resolve()
    missing: list[str] = []

    try:
        model_config.relative_to(model_dir)
    except ValueError:
        return False, ["Model config is outside the configured model directory."], {}

    if not model_config.is_file():
        return False, [str(model_config.relative_to(BASE_DIR))], {}

    try:
        data = json.loads(model_config.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return False, [f"Invalid model.json: {exc}"], {}

    references: list[str] = []
    for key in ("model", "physics", "pose"):
        value = data.get(key)
        if isinstance(value, str) and value:
            references.append(value)

    textures = data.get("textures", [])
    if isinstance(textures, list):
        references.extend(item for item in textures if isinstance(item, str) and item)

    expressions = data.get("expressions", [])
    if isinstance(expressions, list):
        references.extend(
            item["file"] for item in expressions
            if isinstance(item, dict) and isinstance(item.get("file"), str)
        )

    motions = data.get("motions", {})
    if isinstance(motions, dict):
        for group in motions.values():
            if isinstance(group, list):
                references.extend(
                    item["file"] for item in group
                    if isinstance(item, dict) and isinstance(item.get("file"), str)
                )

    for relative in references:
        candidate = (model_dir / relative).resolve()
        try:
            candidate.relative_to(model_dir)
        except ValueError:
            missing.append(f"Invalid path outside model directory: {relative}")
            continue
        if not candidate.is_file():
            missing.append(relative)

    return not missing, missing, data


def model_status() -> dict[str, Any]:
    live2d = CONFIG["live2d"]
    model_dir = (BASE_DIR / live2d["model_dir"]).resolve()
    model_config = (BASE_DIR / live2d["model_config"]).resolve()
    runtime_file = BASE_DIR / "static" / "js" / "live2d.min.js"
    assets_ok, missing, _ = _model_assets()

    with RUNTIME_STATE_LOCK:
        runtime_state = dict(RUNTIME_STATE)

    return {
        "format": live2d.get("format", "MOC2"),
        "name": live2d.get("default_model", "live_uu"),
        "directory_exists": model_dir.is_dir(),
        "config_exists": model_config.is_file(),
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


@app.get("/models/<path:filename>")
def model_asset(filename: str):
    """Serve model assets without exposing files outside the models directory."""
    if not MODEL_ROOT.is_dir():
        abort(404)
    return send_from_directory(MODEL_ROOT, filename, conditional=True)


@app.post("/api/live2d/status")
def update_live2d_status():
    payload = request.get_json(silent=True) or {}
    state = payload.get("state")
    message = payload.get("message", "")

    allowed = {"loading", "loaded", "error"}
    if state not in allowed:
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
