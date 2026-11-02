# Bili Live Danmaku

A modular Bilibili live-danmaku assistant with an OBS-friendly transparent Live2D display.

## Run on Windows

Requirements: Python 3.10 or newer.

1. Download or clone this repository.
2. Double-click \`start.bat\`.
3. On the first launch, the script creates a local \`.venv\` virtual environment and installs dependencies.
4. Open the management page at \`http://127.0.0.1:8768/\`.
5. The transparent OBS display page is at \`http://127.0.0.1:8768/display\`.

Keep the console window open while using the application. Press \`Ctrl+C\` in that window to stop the server.

Manual startup:

\`\`\`powershell
py -3 -m venv .venv
.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.venv\\Scripts\\python.exe main.py
\`\`\`

## Bilibili login and live room

1. Enter your live-room ID in the management page and save it. The value is stored in the local \`user_settings.json\`, not in the tracked project configuration.
2. Click **扫码登录** and scan the QR code with the Bilibili mobile app.
3. After the login is confirmed, the server captures the login cookies from Bilibili's QR-login response and stores them atomically in \`credentials/bilibili.json\`.
4. The server validates the saved login on startup and again before starting a live-room connection. Anonymous connections are rejected.
5. Click **连接直播间**. The page reports connected only after the LiveDanmaku WebSocket receives Bilibili's verification-success event.
6. Unexpected disconnects are retried using the reconnect settings in \`config.json\`. Use **断开连接** to stop the connection.

The credential directory and local settings file are excluded by \`.gitignore\`. Do not share \`credentials/bilibili.json\`; it contains account login credentials. If the Bilibili QR-login flow changes, a successful QR scan is not treated as a successful login until the resulting cookies pass the authenticated navigation API check.

The WEB QR login implementation explicitly follows Bilibili's cross-domain ticket URL after a successful poll. This is necessary for the newer QR-login response format, where login cookies are delivered through response headers rather than the old URL query parameters.

## Live danmaku speech bubble

The \`/display\` page polls the local event queue and displays incoming danmaku above the Live2D model. The bubble is manually positioned by the user:

- Horizontal position and bottom anchor position can be adjusted independently.
- Width and maximum height are adjustable.
- The bottom edge remains fixed while new messages are added from the bottom and the bubble grows upward.
- The maximum number of visible entries and each entry's display duration are adjustable.
- Older entries are removed when the configured item limit is exceeded.
- Each Live2D model stores its own model transform and speech-bubble settings in local \`user_settings.json\`.

Use the settings button at the upper-left of the display page. The sliders preview changes immediately; changes are saved only when **保存设置** is clicked at the bottom of the panel. After saving, the server attempts to connect to OBS WebSocket (default `127.0.0.1:4455`) and refresh browser sources whose URL path is `/display`. If OBS WebSocket is unavailable, authentication fails, or no matching source exists, the settings are still saved and the refresh is skipped.

If OBS WebSocket uses a non-default address, port, or password, add an optional section to the local `config.json`:

```json
"obs_websocket": {
  "host": "127.0.0.1",
  "port": 4455,
  "password": "你的 OBS WebSocket 密码",
  "timeout": 2.5
}
```

Merge this section into the existing top-level JSON object; do not replace the rest of `config.json`. Enable the OBS WebSocket server in OBS under **工具 → WebSocket 服务器设置**. The close (×) control is centered independently of the global button styles. The bubble has a transparent-page-compatible translucent surface and does not add an opaque page background.

## Default Live2D model

The default model is \`live_uu\`:

- Model setting: \`models/live_uu/model.json\` (or \`models/<model-id>/<model-id>.model.json\`)
- Cubism 2 runtime bundle: \`static/js/live2d.min.js\`
- OBS display controller: \`static/js/display.js\`

The display page calls the legacy runtime API \`loadlive2d(canvasId, modelJsonUrl)\`. Flask serves model assets through \`/models/<path>\`. The page checks referenced model files before starting the renderer and reports success only after detecting rendered pixels in the WebGL drawing buffer.

Add the OBS Browser Source URL:

\`\`\`text
http://127.0.0.1:8768/display
\`\`\`

For best clarity, set the OBS Browser Source resolution to the intended output resolution (normally 1920×1080) rather than stretching a small source.

## MOC2 rendering quality

Rendering quality is a first-class requirement. The implementation:

- Sets the WebGL drawing buffer using the displayed CSS dimensions and device-pixel ratio (capped at 2).
- Uses the bundled runtime's texture upload and mipmap path.
- Keeps the page background transparent for OBS.
- Checks that the model actually produces non-transparent WebGL pixels before reporting the runtime as loaded.

MagicalDanmaku is a visual-quality reference only. This project does not copy, port, or derive code or assets from it.

## Current scope

- Bilibili WEB QR-code login and local credential storage
- Login validation before connecting
- Live-room WebSocket connection, reconnection and event reception
- Danmaku, gift and Super Chat event display in the Live2D speech bubble
- Per-model speech-bubble positioning and sizing controls
- Transparent OBS Live2D display

Text-to-speech playback, mouth synchronization and gift-triggered model actions are not implemented yet.

## Development principles

- Keep modules small and independently testable.
- Do not report a feature as implemented until it is actually working.
- Keep credentials and model assets out of version control unless their licenses permit it.
