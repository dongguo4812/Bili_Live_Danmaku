# Bili Live Danmaku

[简体中文](README.zh-CN.md) | **English**

A modular Bilibili live-danmaku assistant with an OBS-friendly transparent Live2D display.

## Run on Windows

Requirements: Python 3.10 or newer.

1. Download or clone this repository.
2. Double-click \`start.bat\`.
3. On the first launch, the script creates a local \`.venv\` virtual environment and installs dependencies.
4. Open the management page at \`http://127.0.0.1:8768/\`.
5. The transparent OBS display page is at \`http://127.0.0.1:8768/live2d/display\`.

Keep the console window open while using the application. Press \`Ctrl+C\` in that window to stop the server.

Manual startup:

\`\`\`powershell
py -3 -m venv .venv
.venv\\Scripts\\python.exe -m pip install -r requirements.txt
.venv\\Scripts\\python.exe main.py
\`\`\`

## Bilibili login and live room

1. Enter your live-room ID in the management page and save it. The value is stored in `config/local/live.json`.
2. Click **扫码登录** and scan the QR code with the Bilibili mobile app.
3. After login is confirmed, the server captures the cookies from Bilibili's WEB QR-login flow and stores them in `credentials/bilibili.json`.
4. If a valid saved credential and room ID are present, the application automatically attempts to connect to that room on startup.
5. Completing QR login also triggers an automatic connection when a room ID has already been saved.
6. The page reports connected only after the LiveDanmaku WebSocket receives Bilibili's verification-success event. Unexpected disconnects are retried using the reconnect settings.
7. Use **断开连接** to stop the live-room connection manually.

Anonymous connections are rejected. A successful QR scan is not treated as a successful login until the resulting cookies pass the authenticated navigation API check. The QR-login implementation follows Bilibili's cross-domain ticket URL so it can capture cookies delivered through response headers.

## Danmaku settings

The `/danmaku` management page controls idle random prompts and reports Bilibili login/live-room connection status. Prompts are shown only while the live connection is authenticated and no new event arrives. Incoming danmaku, gifts or Super Chats immediately clear the prompt. Idle prompts are plain text: they do not trigger Live2D motions or enter the TTS queue. Settings are stored in `config/local/danmaku.json`; existing idle-tip settings in older `live2d.json` files are migrated automatically on first startup.

## Live danmaku speech bubble

The \`/live2d/display\` page polls the local event queue and displays incoming danmaku above the Live2D model. The bubble is manually positioned by the user:

- Horizontal position and bottom anchor position can be adjusted independently.
- Width and maximum height are adjustable.
- The bottom edge remains fixed while new messages are added from the bottom and the bubble grows upward.
- The maximum number of visible entries and each entry's display duration are adjustable.
- Older entries are removed when the configured item limit is exceeded.
- Each Live2D model stores its own model transform and speech-bubble settings in `config/local/live2d.json`.

Use the settings button at the upper-left of the display page. The sliders preview changes immediately; changes are saved only when **保存设置** is clicked at the bottom of the panel. After saving, the server attempts to connect to OBS WebSocket (default `127.0.0.1:4455`) and refresh browser sources whose URL path is `/live2d/display`. If OBS WebSocket is unavailable, authentication fails, or no matching source exists, the settings are still saved and the refresh is skipped.

OBS WebSocket ordinary settings are stored in `config/local/obs.json`. Enter the password separately on the system settings page; it is stored in `credentials/obs.json` and protected with Windows DPAPI. The UI never reads back or displays the saved password.

System settings: `http://127.0.0.1:8768/settings`. Enable the OBS WebSocket server in OBS under **工具 → WebSocket 服务器设置**.

## Live2D model runtimes

The application supports two isolated Live2D runtimes:

| Runtime | Model directory | Model entry | Runtime assets |
|---|---|---|---|
| MOC2 | `models/moc2/<model-id>/` | `model.json` or `<directory-name>.model.json` | `static/js/live2d.min.js` |
| MOC3 | `models/moc3/<model-id>/` | `<directory-name>.model3.json` | Cubism 5 R5 Core / Framework under `static/js/live2d/moc3/` |

The full display page at `/live2d/display` loads the selected runtime from `config/local/live2d.json`. Model directories, selections and per-model preferences are kept separate between MOC2 and MOC3. The configured default MOC2 model ID is `live_uu`; if it is not installed locally, select an installed model in the display settings or add a properly licensed model under the MOC2 directory. When no MOC3 default is configured, the first available MOC3 model is selected.

The model capability panel checks model files, textures, motions, expressions, physics, HitArea mappings and TTS mouth parameters. MOC3 motions, expressions, keyboard shortcuts and custom hit-area mappings are handled by the Cubism 5 runtime. Availability still depends on the features declared by each model.

TTS mouth synchronization is driven by the audio actually played in the display page. Models with a usable LipSync group or compatible mouth parameter can synchronize. A model with neither is reported as unsupported; this is a model-resource limitation, not a runtime loading failure.

Add model assets only when their licenses permit their use, and ensure all referenced textures, motions, expressions and physics files are present.

## Live2D rendering quality

Rendering quality is a first-class requirement. The implementation:

- Sets the WebGL drawing buffer using the displayed CSS dimensions and device-pixel ratio (capped at 2).
- Uses the bundled runtime's texture upload and mipmap path.
- Keeps the page background transparent for OBS.
- Checks that the model actually produces non-transparent WebGL pixels before reporting the runtime as loaded.

MagicalDanmaku is a visual-quality reference only. This project does not copy, port, or derive code or assets from it.

## Unified modules and combined display

The manager is available at `http://127.0.0.1:8768/`. The sidebar links to the access center, Live2D, danmaku settings, voice, music, video, system settings and the combined display editor.

- Music and video have independent lifecycle controls, queues and configuration. They do not pause or resume each other.
- Music and video start closed after each application launch.
- The music module searches QQ Music, Kugou, NetEase Cloud Music and Kuwo in order, stores matched source IDs at enqueue time, verifies playback for five seconds and falls back only to pre-saved source candidates.
- The video module retains Chrome/Selenium playback, BV validation, queue management, playback monitoring and OBS source visibility. Web fullscreen is attempted only after playback progress is detected; the module has no LX Music control calls.
- The combined layout editor is `/dashboard`. It supports multiple named layouts; each layout has its own OBS URL at `/display/combined/<layout-id>`. The legacy `/display/combined` URL continues to show the default layout.
- The video widget shows current video, next item, queue and notifications only; it does not embed the video player.
- Music and video share the system administrator UID list configured on `/settings`; do not create per-module administrator files.
- Dedicated transparent display URLs: the complete Live2D + danmaku + TTS display is `/live2d/display`; the MOC2-only display is `/live2d/moc2/display`; standalone danmaku and voice displays are `/danmaku/display` and `/voice/display`. Music queue, video queue and music-widget displays are `/music/display`, `/video/display` and `/music/widget`.
- Live2D settings APIs use the `/api/live2d/` namespace.

## Current scope

- Bilibili WEB QR-code login and local credential storage
- Login validation before connecting
- Live-room WebSocket connection, reconnection and event reception
- Danmaku, gift and Super Chat event display in the Live2D speech bubble
- Edge TTS speech for danmaku, gifts and Super Chat, with independent switches
- Configurable Chinese voice, speech rate, volume, queue limit and minimum gift value
- Super Chat and gifts share the high-priority speech tier; ordinary danmaku is lower priority
- When the waiting queue is full, the oldest waiting ordinary-danmaku speech is discarded first; current playback is never interrupted
- Audio-level-driven mouth synchronization through the selected MOC2 or MOC3 runtime, when the model exposes a compatible mouth parameter
- Per-model speech-bubble positioning and sizing controls
- Transparent OBS Live2D display

Configure Edge TTS on the `/voice` management page. The `/live2d/display` page keeps only the runtime needed to play speech and synchronize the model mouth. The display page automatically attempts to initialize and resume Web Audio, then processes the speech queue after the live-room connection is established. Browser or OBS autoplay policies may still affect playback; if audio does not start, check the OBS Browser Source audio/autoplay settings. The **生成并试听** control on `/voice` remains available to diagnose audio output and mouth synchronization. Generated MP3 files are kept in a temporary system folder; files older than one hour are removed during a later synthesis request.

## Development principles

- Keep modules small and independently testable.
- Do not report a feature as implemented until it is actually working.
- Keep credentials and model assets out of version control unless their licenses permit it.
