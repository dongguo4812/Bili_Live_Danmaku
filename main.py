# -*- coding: utf-8 -*-
"""Bili Live Danmaku application entry point.

This is an initial web/API scaffold. Bilibili login, live-room transport,
TTS, and the MOC2 runtime are intentionally not reported as implemented yet.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bili_live_danmaku")


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(f"Configuration file not found: {CONFIG_PATH}")
    with CONFIG_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("config.json must contain a JSON object")
    return data


CONFIG = load_config()
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))


def model_status() -> dict[str, Any]:
    model_dir = BASE_DIR / CONFIG["live2d"]["model_dir"]
    model_config = BASE_DIR / CONFIG["live2d"]["model_config"]
    return {
        "format": CONFIG["live2d"].get("format", "MOC2"),
        "name": CONFIG["live2d"].get("default_model", "live_uu"),
        "directory_exists": model_dir.is_dir(),
        "config_exists": model_config.is_file(),
        "runtime_ready": False,
        "message": "MOC2 runtime has not been integrated yet.",
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/display")
def display():
    return render_template("display.html")


@app.get("/api/status")
def api_status():
    return jsonify({
        "application": "Bili Live Danmaku",
        "web_server": "running",
        "bilibili": {
            "logged_in": False,
            "connected": False,
            "message": "Bilibili QR login and live connection are not implemented yet.",
        },
        "live2d": model_status(),
        "features": {
            "danmaku": False,
            "gift_events": False,
            "tts": False,
            "mouth_sync": False,
            "moc2_runtime": False,
        },
    })


if __name__ == "__main__":
    host = CONFIG["app"].get("host", "127.0.0.1")
    port = int(CONFIG["app"].get("port", 8768))
    debug = bool(CONFIG["app"].get("debug", False))
    logger.info("Starting local management server at http://%s:%s", host, port)
    app.run(host=host, port=port, debug=debug, threaded=True)
