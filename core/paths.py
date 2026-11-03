# -*- coding: utf-8 -*-
"""统一项目根目录定位，兼容源码运行与 PyInstaller onedir。"""
from __future__ import annotations

import sys
from pathlib import Path


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]
