# Bili Live Danmaku

A new, modular Bilibili live-danmaku assistant with an OBS-friendly transparent Live2D display.

## Project status

This repository is being built from a clean baseline. The initial scaffold is intentionally small; features will be added in verifiable stages.

## Planned features

- Bilibili QR-code login and secure local credential storage
- Cookie validation before connecting to a live room
- Live-room WebSocket connection and event reception
- Danmaku and gift event handling
- Edge TTS speech playback with mouth movement synchronization
- Gift-specific actions, membership expressions, and configurable danmaku emotions
- Transparent OBS browser display and a local management page
- MOC2 (Live2D Cubism 2) model support

## Default model

The planned default model is `live_uu`, expected at:

```text
models/live_uu/model.json
```

Model assets are not included in this repository. Add them locally only after confirming that their license permits the intended use and distribution.

## MOC2 rendering quality

Rendering quality is a first-class requirement. The implementation should:

- Size the WebGL drawing buffer using the displayed CSS dimensions and device-pixel ratio.
- Use appropriate texture filtering, mipmapping, alpha blending, and mask-buffer resolution.
- Avoid upscaling a low-resolution OBS browser source.
- Be validated with actual model screenshots at the target output resolution.

MagicalDanmaku is a visual-quality reference only. This project will not copy, port, or derive code or assets from it. Its archived repository explicitly restricts reuse.

## Development principles

- Keep modules small and independently testable.
- Do not report a feature as implemented until it is actually working.
- Keep credentials and model assets out of version control.
- Prefer documented, legally usable MOC2 runtime components.

## Initial setup

Python dependencies and run instructions will be added as the application scaffold is implemented.
