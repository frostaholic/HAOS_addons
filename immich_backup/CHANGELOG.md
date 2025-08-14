# Change Log

## 1.1.5 — 2025-08-12
- Bug: **Sensor Throttle** throttle: push HA state once/min + on status change
- 
## 1.1.4 — 2025-08-12
- Change: **Config Page Declutter** added some Configuration options to advanced. declutter settings
- 
## 1.1.3 — 2025-08-11
- Change: **Disabled the automatic initial export on add-on start** to avoid HA startup/backup contention and surprise heavy loads.
- Feature: **“Run backup now”** button in the web UI (`/run-now` endpoint) to manually trigger an export on demand.
- Feature: **Live status** in the UI via `/status` (plus existing `/progress`); the button auto-disables while a run is active.
- Safety: Simple **file-lock** prevents overlapping runs (button vs cron).
- Internals: Removed initial export call from `run.sh`; `webgui.py` now launches the exporter in a background thread.
- Compatibility: **Cron schedules are unchanged** and continue to run as configured.
- Migration: No action required. If you previously relied on the automatic first run, click **Run backup now** after install/update.


## 1.1.2 — 2025-08-09
- Fix: reduce conflicts with HA Core backup/updates by deferring the initial export until after startup and easing DB pressure.
- Feature: new **overall % copied** sensor (processed / total) for clearer dashboard progress.
- Docs: README/screenshot and sample dashboard notes.
- Repo: lives under `HAOS_addons/immich_backup`.

## 1.1.1 (2025-08-08)
- Safety: add deletion guard to prevent mass-deletes when source is unavailable.
  - New env/options: `MIN_FOUND_ABS` (default 100) and `MIN_FOUND_FRACTION` (default 0.05).
  - Guard reason exposed in `progress.json` as `guard`.
- Paths: auto-detect `upload/` vs `upload/upload/`.
- UI: add **advanced** options `min_found_abs` and `min_found_fraction` (hidden until “Show unused options”); clearer grouped labels.
- DB: detect join table (`albums_assets` vs `albums_assets_assets`) and enable autocommit to avoid aborted transactions.
- GUI: read `EXPORT_DIR` from env; better error handling for empty/malformed `progress.json`.
- Cron/health: quieter cron logs; removed/updated failing healthcheck (use GUI port or none).

## 1.1.0 (2025-08-08)
Bump version to `1.1.0`. 

## 1.0.9 (2025-08-08)
- remove the confusing db_path_prefix / mount_prefix and use a single ASSETS_ROOT

## 1.0.8 (2025-08-07)
- Rework Configuration Page

## 1.0.7 (2025-08-07)
- Require `EXPORT_DIR` env var in `webgui.py` (no default fallback) to avoid hard-coded paths.
- Enhance web GUI error handling for missing or malformed `progress.json`.
- Ensure `run.sh` exports all env vars **before** launching the web GUI.

## 1.0.6 (2025-08-07)
- Introduce embedded Flask web GUI (`webgui.py`) served via Supervisor Ingress.
- Add `ingress: true` and `ingress_port: 5000` to `config.yaml`.
- Update `run.sh` to start the Flask server alongside the cron daemon.

## 1.0.5 (2025-08-06)
- Enable Ingress support in the add-on manifest for UI integration.
- Install Flask in Dockerfile via Alpine’s `py3-flask` package.
- Bump version to `1.0.5`.

## 1.0.4 (2025-08-05)
- Refactor `map:` entries in `config.yaml` to use `host`/`container` dictionary syntax.
- Narrow down host mounts for improved security rating.
- Remove `host_network: true` from config.

## 1.0.3 (2025-08-05)
- Add support for mounting NFS shares via optional UI fields.
- Bump version to `1.0.3`.

## 1.0.2 (2025-07-15)
- Add UI options `db_path_prefix` and `mount_prefix` for path translation.
- Refactor `run.sh` to export and pass `DB_PATH_PREFIX`/`MOUNT_PREFIX` into the Python script.
- Bump version to `1.0.2`.

## 1.0.1 (2025-07-01)
- Initial release: export Immich albums to filesystem with scheduled backups.
- Supports cron scheduling, progress logging, and asset validation.
- GUI: read `EXPORT_DIR` from env; better error handling for empty/malformed `progress.json`.
- Cron/health: quieter cron logs; removed/updated failing healthcheck (use GUI port or none).

## 1.1.0 (2025-08-08)
Bump version to `1.1.0`. 

## 1.0.9 (2025-08-08)
- remove the confusing db_path_prefix / mount_prefix and use a single ASSETS_ROOT

## 1.0.8 (2025-08-07)
- Rework Configuration Page

## 1.0.7 (2025-08-07)
- Require `EXPORT_DIR` env var in `webgui.py` (no default fallback) to avoid hard-coded paths.
- Enhance web GUI error handling for missing or malformed `progress.json`.
- Ensure `run.sh` exports all env vars **before** launching the web GUI.

## 1.0.6 (2025-08-07)
- Introduce embedded Flask web GUI (`webgui.py`) served via Supervisor Ingress.
- Add `ingress: true` and `ingress_port: 5000` to `config.yaml`.
- Update `run.sh` to start the Flask server alongside the cron daemon.

## 1.0.5 (2025-08-06)
- Enable Ingress support in the add-on manifest for UI integration.
- Install Flask in Dockerfile via Alpine’s `py3-flask` package.
- Bump version to `1.0.5`.

## 1.0.4 (2025-08-05)
- Refactor `map:` entries in `config.yaml` to use `host`/`container` dictionary syntax.
- Narrow down host mounts for improved security rating.
- Remove `host_network: true` from config.

## 1.0.3 (2025-08-05)
- Add support for mounting NFS shares via optional UI fields.
- Bump version to `1.0.3`.

## 1.0.2 (2025-07-15)
- Add UI options `db_path_prefix` and `mount_prefix` for path translation.
- Refactor `run.sh` to export and pass `DB_PATH_PREFIX`/`MOUNT_PREFIX` into the Python script.
- Bump version to `1.0.2`.

## 1.0.1 (2025-07-01)
- Initial release: export Immich albums to filesystem with scheduled backups.
- Supports cron scheduling, progress logging, and asset validation.

