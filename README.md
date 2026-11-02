# Bili Live Danmaku

A modular Bilibili live-danmaku assistant with an OBS-friendly transparent Live2D display.

## Run on Windows

Requirements: Python 3.10 or newer.

1. Download or clone this repository.
2. Double-click `start.bat`.
3. On the first launch, the script creates a local `.venv` virtual environment and installs dependencies.
4. The management page opens at `http://127.0.0.1:8768/`.
5. The transparent OBS display page is at `http://127.0.0.1:8768/display`.

Keep the console window open while using the application. Press `Ctrl+C` in that window to stop the server.

Manual startup:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## Default Live2D model

The default model is `live_uu`:

- Model setting: `models/live_uu/model.json`
- Cubism 2 runtime bundle: `static/js/live2d.min.js`
- OBS display controller: `static/js/display.js`

The display page calls the legacy runtime API `loadlive2d(canvasId, modelJsonUrl)`. Flask serves model assets through `/models/<path>`. The page checks referenced model files before starting the renderer and reports success only after detecting rendered pixels in the WebGL drawing buffer.

Add the OBS Browser Source URL:

```text
http://127.0.0.1:8768/display
```

For best clarity, set the OBS Browser Source resolution to the intended output resolution (normally 1920×1080) rather than stretching a small source.

## MOC2 rendering quality

Rendering quality is a first-class requirement. The implementation:

- Sets the WebGL drawing buffer using the displayed CSS dimensions and device-pixel ratio (capped at 2).
- Uses the bundled runtime's texture upload and mipmap path.
- Keeps the page background transparent for OBS.
- Checks that the model actually produces non-transparent WebGL pixels before reporting the runtime as loaded.

MagicalDanmaku is a visual-quality reference only. This project does not copy, port, or derive code or assets from it.

## Planned features

- Bilibili QR-code login and secure local credential storage
- Cookie validation before connecting to a live room
- Live-room WebSocket connection and event reception
- Danmaku and gift event handling
- Edge TTS speech playback with mouth movement synchronization
- Gift-specific actions, membership expressions, and configurable danmaku emotions

## Development principles

- Keep modules small and independently testable.
- Do not report a feature as implemented until it is actually working.
- Keep credentials and model assets out of version control unless their licenses permit it.
