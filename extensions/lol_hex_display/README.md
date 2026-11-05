# LOL Hex Display

LOL Hex is a standalone Flask application that can also be managed as an extension by the Bili Live Danmaku host application.

## Pages

| Page | Standalone URL | Host-managed URL |
|---|---|---|
| Manager | `http://127.0.0.1:8767/` | `/extensions/lol-hex/` |
| Augment display | `http://127.0.0.1:8767/display` | `/extensions/lol-hex/display` |
| Match-history bar | `http://127.0.0.1:8767/bar` | `/extensions/lol-hex/bar` |

The default service port is `8767`.

## Host-managed mode

- The host application's access center starts and stops the extension process.
- The extension remains responsible for League Client recognition, F1 screenshots, local OCR, augment data and its own display pages.
- On Windows, the host requests administrator elevation when starting Hex. The F1 global screenshot hotkey requires administrator privileges; allow the UAC prompt.
- The host application is the sole owner of OBS WebSocket connections, reconnection and browser-source refreshes. Hex does not create a second OBS WebSocket connection in host-managed mode.
- The combined-display editor exposes the match-history bar and augment display as separate widgets.

## Configuration

The host application uses:

- Default template: `config/default/lol_hex_display.json`
- Local configuration: `config/local/lol_hex_display.json`

The local JSON contains the service host/port, screenshot directory, scan interval, asset/version/gameflow check intervals and League client installation path. Edit the local file for machine-specific values; do not put personal paths in the tracked default template.

During an upgrade, if the local JSON does not exist and the legacy `extensions/lol_hex_display/config.txt` is present, recognized values are migrated into the local JSON. An existing local JSON always takes precedence.

When launched standalone, the application accepts `--config <path>`; without that argument it falls back to `config.json` beside the application. If no such file exists, built-in defaults are used.

## Local runtime files

The extension creates these directories as needed:

- `data/`: OCR/augment metadata cache and runtime state.
- `screenshots/inbox/`: newly captured screenshots awaiting processing.
- `screenshots/processed/`: screenshots already processed.
- `logs/`: application logs and periodic thread diagnostics.
- `lol_hex_display.pid`: current process ID used by the host manager.

These are machine-local runtime artifacts and are ignored by the repository's `.gitignore`. Python may also create `__pycache__/` directories during source execution; those are ignored as well.

The extension's own `build.bat` creates a folder-based EXE distribution without bundling local runtime data. The root host application's `build.bat` intentionally copies any existing Hex `data/`, `screenshots/` and `logs/` into its local distribution folder. Git ignore rules do not affect this local packaging step.
