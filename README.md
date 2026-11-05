# Bili Live Danmaku

[简体中文](README.zh-CN.md) | **English**

A modular Bilibili live-stream assistant with danmaku, Live2D, Edge TTS, music/video requests, LOL Hex displays and OBS integration.

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

The management center is available at `http://127.0.0.1:8768/`. The access center provides one **功能启停控制** panel below the Bilibili login and live-room connection section:

- Music, video and voice each have a start/close toggle and a pause/resume toggle. Live2D has one enable/disable toggle.
- The panel reports actual module states with consistent colors: green for healthy/running/connected/enabled, yellow for paused, blue for waiting or transitioning, gray for stopped/disabled/disconnected, and red for errors.
- Module detail pages retain their own queue, skip, request-switch, configuration and audio-test actions. Lifecycle controls are centralized in the access center.
- Music and video are independent; lifecycle actions on one do not change the other's state or playback.
- Music and video start closed on each application launch. Voice starts automatically after the first authenticated Bilibili WebSocket connection. Live2D has its own enable/disable setting and does not require a live-room connection.
- On a live-room disconnect, music, video and voice are paused. After reconnection, only modules that were running before the disconnect and were not manually changed are resumed.
- Music searches QQ Music, Kugou, NetEase Cloud Music and Kuwo in order, stores matched source IDs at enqueue time, verifies playback for five seconds and falls back only to saved source candidates.
- Video uses Chrome/Selenium, validates BV IDs, monitors playback and controls OBS source visibility. Web fullscreen is attempted only after playback progress is detected; video has no LX Music control calls.
- Music settings: `/music/settings`; video settings: `/video/settings`. Stop the corresponding module before changing its configuration.
- Music and video share the system administrator UID list configured on `/settings`.
- Display URLs: full Live2D/danmaku display `/live2d/display`; MOC2-only display `/live2d/moc2/display`; danmaku `/danmaku/display`; dedicated global voice engine `/voice/display`; music queue `/music/display`; video queue `/video/display`; music widget `/music/widget`.
- Live2D settings APIs use the `/api/live2d/` namespace. The module lifecycle API is shared by the access-center controls.
- The combined layout editor is `/dashboard`. It supports multiple named layouts, and each layout can use its own OBS URL at `/display/combined/<layout-id>`. The legacy `/display/combined` URL continues to show the default layout.
- When OBS WebSocket becomes available, the application refreshes matching combined-display browser sources (`/display/combined` or `/display/combined/<layout-id>`) and ensures that the shared global voice-engine Browser Source is present in every scene of the active scene collection. The hidden 2×2 source is configured to remain loaded while scenes change. This is independent of Bilibili connection state; if OBS is unavailable, discovery is retried every three seconds.
- After clicking **保存当前布局** in the combined-display layout editor, the application asynchronously refreshes matching combined-display sources and rechecks the global voice-engine source. This does not block saving; if OBS is unavailable, the save still succeeds and that refresh is skipped.
- The application does not change visibility of the combined-display sources. It only creates/enables the dedicated global voice-engine scene item; do not delete or disable that source.
- The video widget shows current video, next item, queue and notifications only; it does not embed the video player.


### LOL Hex extension

LOL Hex runs as a separate process. The access center only starts and stops it; its game recognition, F1 screenshots, OCR, augment display and match-history bar remain in the extension.

- On Windows, starting the extension requests elevation. The F1 global screenshot hotkey requires administrator privileges; cancelling the UAC prompt prevents startup.
- In host-managed mode, the main application owns OBS WebSocket connections, reconnection and browser-source refreshes. LOL Hex does not open a second OBS WebSocket connection.
- Host-managed pages: manager `/extensions/lol-hex/`, augment display `/extensions/lol-hex/display`, and match-history bar `/extensions/lol-hex/bar`. Standalone mode defaults to port `8767` (`http://127.0.0.1:8767/`, `/display`, and `/bar`).
- The tracked default template is `config/default/lol_hex_display.json`; the machine-specific file is `config/local/lol_hex_display.json`. On upgrade, a legacy `extensions/lol_hex_display/config.txt` is migrated only when the local JSON file does not yet exist.
- Local runtime data lives under `extensions/lol_hex_display/data/`, `screenshots/` and `logs/`; `lol_hex_display.pid` records the process ID. These files, as well as Python `__pycache__/`, are ignored by Git.
- The extension's own build excludes local runtime data. The root `build.bat` intentionally copies existing local Hex data, screenshots and logs into the folder-based distribution. This packaging behavior is separate from Git tracking.
- The combined-display editor treats **LOL match-history bar** and **LOL augment display** as independent widgets.
- See the [LOL Hex extension guide](extensions/lol_hex_display/README.md) for standalone and host-managed operation.\n- Music and video display queue limits are configured independently on `/music/settings` and `/video/settings` (1–1000, default 10). The displayed pending total still reflects the full queue.


GitHub Actions runs Python compilation, configuration and offline regression tests on Ubuntu, plus real DPAPI and retired-configuration checks on Windows. Passing CI does not mean every device has completed manual integration testing; music and video still need local validation with LX Music, Chrome, OBS WebSocket and a real live room.

## Current scope

- Bilibili WEB QR-code login and local credential storage
- Saved-credential validation and automatic connection to the saved live room on startup
- Live-room WebSocket connection, reconnection and event reception
- Danmaku, gift and Super Chat event display in the Live2D speech bubble
- Edge TTS speech for danmaku, gifts and Super Chat, with independent switches
- Configurable Chinese voice, speech rate, volume, queue limit and minimum gift value
- Super Chat and gifts share the high-priority speech tier; ordinary danmaku is lower priority
- When the waiting queue is full, the oldest waiting ordinary-danmaku speech is discarded first; current playback is never interrupted
- Audio-level-driven mouth synchronization through the selected MOC2 or MOC3 runtime, when the model exposes a compatible mouth parameter
- MOC2/MOC3 model separation, per-model speech-bubble positioning and sizing controls
- MOC3 motion/expression playback, keyboard shortcuts, custom HitArea mappings and model capability checks
- Transparent OBS Live2D display through the selected MOC2 or MOC3 runtime

Configure Edge TTS on the `/voice` management page. Speech is consumed by one dedicated OBS voice-engine page rather than by every combined-display page. After connecting to OBS WebSocket, the application creates or reuses a hidden 2×2 Browser Source named `B站直播弹幕助手-全局语音引擎` and adds the same OBS input to every scene in the active scene collection. The shared source is configured not to shut down or refresh when a scene becomes active/inactive, so changing scenes does not interrupt the current utterance or create a second player. Live2D display pages mirror the shared playback level for mouth synchronization; only the model visible in the current scene is seen moving. Do not delete or disable the automatically managed voice-engine source. The `/voice` preview button enqueues its text through this same OBS engine. Generated MP3 files are kept in a temporary system folder; files older than one hour are removed during a later synthesis request.

## Verification status

The maintainer has manually verified saved-room startup connection, access-center module controls and status colors, MOC2/MOC3 switching and model isolation, MOC3 actions/expressions/shortcuts/HitArea mappings, and model capability reporting. TTS mouth synchronization works on models with compatible mouth parameters; models without those parameters are correctly reported as unsupported. Gift and Super Chat event paths still need a separate complete end-to-end check. Music and video playback must be validated with the local LX Music, Chrome, OBS and live-room environment.

## Development principles

- Keep modules small and independently testable.
- Do not report a feature as implemented until it is actually working.
- Never commit credentials, passwords or local user configuration.
- Only include model assets when their licenses explicitly permit repository storage and redistribution.
