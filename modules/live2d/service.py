# -*- coding: utf-8 -*-
"""Live2D 展示生命周期标记；模型渲染仍由现有 OBS 浏览器页执行。"""
from __future__ import annotations


class Live2DModule:
    def __init__(self) -> None:
        self.enabled = True
        self.paused = False

    async def start(self) -> None:
        self.enabled = True
        self.paused = False

    async def pause(self) -> None:
        if self.enabled:
            self.paused = True

    async def resume(self) -> None:
        if self.enabled:
            self.paused = False

    async def close(self) -> None:
        self.enabled = False
        self.paused = False
