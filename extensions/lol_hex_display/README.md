# LOL Hex Display

LOL Hex Display is a League of Legends augment recognition and match-history display extension. It can run independently or be managed by the Bili Live Danmaku host application.

## Pages

| Page | Standalone URL | Host-managed URL |
|---|---|---|
| Manager | `http://127.0.0.1:8767/` | `/extensions/lol-hex/` |
| Augment display | `http://127.0.0.1:8767/display` | `/extensions/lol-hex/display` |
| Match-history bar | `http://127.0.0.1:8767/bar` | `/extensions/lol-hex/bar` |

The default service port is `8767`.

## Manager

The manager page provides controls for the extension and its local data:

- **Client and account status:** shows whether a usable League Client has been detected, whether the LCU connection is available, and the recognized account when the information is available.
- **Screenshot capture:** press **F1** during a game to freeze the screen and select a region. Press **F1** again or **Esc** to cancel the selection.
- **Screenshot and data management:** review captured screenshots and generated cards, open the screenshot directory, and clear cards or screenshots when needed.
- **Augment index maintenance:** check for and update the augment index, or reset the index when a rebuild is required.

If OCR cannot confidently identify an augment, the screenshot is retained for visual review instead of displaying an invented name. A failed screenshot-file deletion keeps an internal processed marker so the same item is not repeatedly recognized and recreated.

## Display behavior

- The augment display presents recognized augment information. When recognition does not produce a reliable name match, it displays the screenshot for manual inspection.
- The match-history bar displays the match-related cards produced by the extension.
- The host application's combined-display editor can place the augment display and match-history bar as separate widgets. Their scale and position are controlled by the host layout.
- The extension periodically checks League Client availability and account identity. Account information is cached for display and related processing.

## Host-managed mode

- Start and stop the extension from the host application's access center.
- Hex remains responsible for League Client recognition, screenshots, OCR, augment data and its own display pages.
- On Windows, the host requests administrator elevation when starting Hex. The global **F1** screenshot hotkey requires administrator privileges; allow the UAC prompt.
- The host application is the sole owner of OBS WebSocket connections, reconnection and browser-source refreshes. Hex does not create a second OBS WebSocket connection in host-managed mode.
- The combined-display editor exposes the augment display and match-history bar as separate widgets.

## Configuration

The host application uses:

- Default template: `config/default/lol_hex_display.json`
- Local configuration: `config/local/lol_hex_display.json`

The local JSON contains machine-specific settings such as the service host/port, screenshot directory, scan interval, resource/version/gameflow check intervals and League Client installation path. Edit the local file for machine-specific values; do not put personal paths in the tracked default template.

During an upgrade, if the local JSON does not exist and the legacy `extensions/lol_hex_display/config.txt` is present, recognized values are migrated into the local JSON. An existing local JSON always takes precedence.

When launched standalone, the application accepts `--config <path>`; without that argument it falls back to `config.json` beside the application. If no such file exists, built-in defaults are used.

## Local runtime files

The extension creates these directories as needed:

- `data/`: OCR/augment metadata cache and runtime state.
- `screenshots/inbox/` (default): captured screenshots and their source files; the configured screenshot directory is used at runtime.
- `logs/`: application logs and periodic thread diagnostics.
- `lol_hex_display.pid`: current process ID used by the host manager.

These are machine-local runtime artifacts and are ignored by the repository's `.gitignore`. Python may also create `__pycache__/` directories during source execution; those are ignored as well.

The extension's `build.bat` creates a folder-based EXE distribution without bundling local runtime data. The root host application's `build.bat` intentionally copies any existing Hex `data/`, `screenshots/` and `logs/` into its local distribution folder. Git ignore rules do not affect this local packaging step.
