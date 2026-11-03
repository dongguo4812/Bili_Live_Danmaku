# -*- coding: utf-8 -*-
"""语音播报生命周期标记；Edge TTS 音频队列由 Live2D 浏览器页播放。"""
from __future__ import annotations


class VoiceModule:
    def __init__(self) -> None:
        self.enabled = False
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
