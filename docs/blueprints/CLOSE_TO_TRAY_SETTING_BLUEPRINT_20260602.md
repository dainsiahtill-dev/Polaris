# Close-To-Tray Setting Blueprint

Date: 2026-06-02

## Goal

Make the desktop window close behavior user-configurable through the existing settings panel and persisted global settings file.

## Data Flow

```text
SettingsModal checkbox
  -> POST /settings close_to_tray
  -> Settings.close_to_tray
  -> ~/.polaris/config/settings.json
  -> Electron close handler reads settings.json
  -> hide to tray or allow app quit
```

## Responsibilities

- Frontend settings panel owns user interaction and save payload.
- Backend settings model owns validation, in-memory update, and persistence.
- Electron owns runtime window close behavior and reads the persisted setting at close time.

## Compatibility

Default `close_to_tray` is `true`, preserving existing behavior. Missing, invalid, or unreadable settings also fall back to `true`.

## Verification

- Python config tests confirm default, update, and payload fields.
- Frontend SettingsModal test confirms checkbox save payload.
- Node policy tests confirm Electron setting interpretation.
