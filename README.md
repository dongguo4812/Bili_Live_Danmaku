# Bili Live Danmaku

[简体中文](README.zh-CN.md) | **English**

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

1. Enter your live-room ID in the management page and save it. The value is stored in `config/live.json`.
2. Click **扫码登录** and scan the QR code with the Bilibili mobile app.
3. After the login is confirmed, the server captures the login cookies from Bilibili's QR-login response and stores them atomically in \`credentials/bilibili.json\`.
4. The server validates the saved login on startup and again before starting a live-room connection. Anonymous connections are rejected.
5. Click **连接直播间**. The page reports connected only after the LiveDanmaku WebSocket receives Bilibili's verification-success event.
6. Unexpected disconnects are retried using the reconnect settings in `config/live.json`. Use **断开连接** to stop the connection.

The credential directory and local settings file are excluded by \`.gitignore\`. Do not share \`credentials/bilibili.json\`; it contains account login credentials. If the Bilibili QR-login flow changes, a successful QR scan is not treated as a successful login until the resulting cookies pass the authenticated navigation API check.

The WEB QR login implementation explicitly follows Bilibili's cross-domain ticket URL after a successful poll. This is necessary for the newer QR-login response format, where login cookies are delivered through response headers rather than the old URL query parameters.

## Live danmaku speech bubble

The \`/display\` page polls the local event queue and displays incoming danmaku above the Live2D model. The bubble is manually positioned by the user:

- Horizontal position and bottom anchor position can be adjusted independently.
- Width and maximum height are adjustable.
- The bottom edge remains fixed while new messages are added from the bottom and the bubble grows upward.
- The maximum number of visible entries and each entry's display duration are adjustable.
- Older entries are removed when the configured item limit is exceeded.
- Each Live2D model stores its own model transform and speech-bubble settings in `config/live2d.json`.

Use the settings button at the upper-left of the display page. The sliders preview changes immediately; changes are saved only when **保存设置** is clicked at the bottom of the panel. After saving, the server attempts to connect to OBS WebSocket (default `127.0.0.1:4455`) and refresh browser sources whose URL path is `/display`. If OBS WebSocket is unavailable, authentication fails, or no matching source exists, the settings are still saved and the refresh is skipped.

OBS WebSocket ordinary settings are stored in `config/obs.json`. Enter the password separately on the system settings page; it is stored in `credentials/obs.json` and protected with Windows DPAPI. The UI never reads back or displays the saved password.

System settings: `http://127.0.0.1:8768/settings`. Enable the OBS WebSocket server in OBS under **工具 → WebSocket 服务器设置**.

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

## Unified modules and combined display

The manager is available at `http://127.0.0.1:8768/`. The sidebar links to the access center, Live2D, voice, music, video, system settings and the combined display editor.

- Music and video have independent lifecycle controls, queues and configuration. They do not pause or resume each other.
- Music and video start closed after each application launch.
- The music module searches QQ Music, Kugou, NetEase Cloud Music and Kuwo in order, stores matched source IDs at enqueue time, verifies playback for five seconds and falls back only to pre-saved source candidates.
- The video module retains Chrome/Selenium playback, BV validation, queue management, playback monitoring and OBS source visibility. Web fullscreen is attempted only after playback progress is detected; the module has no LX Music control calls.
- The combined layout editor is `/dashboard`. It supports multiple named layouts; each layout has its own OBS URL at `/display/combined/<layout-id>`. The legacy `/display/combined` URL continues to show the default layout.
- The video widget shows current video, next item, queue and notifications only; it does not embed the video player.
- Music and video share the system administrator UID list configured on `/settings`; do not create per-module administrator files.
- Dedicated transparent display URLs are available: `/display/live2d`, `/display/danmaku`, `/display/tts`, `/display/music`, `/display/video` and `/display/music-widget`. The legacy `/display` remains the complete Live2D + danmaku + TTS display. Existing `/music/display`, `/music/widget` and `/video/display` URLs remain supported.
- Live2D settings APIs use the `/api/live2d/` namespace. The former `/api/display/settings` endpoints remain as compatibility aliases.

## Current scope

- Bilibili WEB QR-code login and local credential storage
- Login validation before connecting
- Live-room WebSocket connection, reconnection and event reception
- Danmaku, gift and Super Chat event display in the Live2D speech bubble
- Edge TTS speech for danmaku, gifts and Super Chat, with independent switches
- Configurable Chinese voice, speech rate, volume, queue limit and minimum gift value
- Super Chat and gifts share the high-priority speech tier; ordinary danmaku is lower priority
- When the waiting queue is full, the oldest waiting ordinary-danmaku speech is discarded first; current playback is never interrupted
- Audio-level-driven mouth synchronization through the Cubism 2 MOC2 runtime
- Per-model speech-bubble positioning and sizing controls
- Transparent OBS Live2D display

Open the settings panel on the `/display` page to configure Edge TTS. The display page automatically attempts to initialize and resume Web Audio, then processes the speech queue after the live-room connection is established. Browser or OBS autoplay policies may still affect playback; if audio does not start, check the OBS Browser Source audio/autoplay settings. The **测试语音** button remains available to diagnose audio output and mouth synchronization. Generated MP3 files are kept in a temporary system folder; files older than one hour are removed during a later synthesis request.

## Development principles

- Keep modules small and independently testable.
- Do not report a feature as implemented until it is actually working.
- Keep credentials and model assets out of version control unless their licenses permit it.
