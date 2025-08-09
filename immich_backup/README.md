
# Immich Album Export Home Assistant Add-on

Export your **Immich** library into **human-readable album folders** instead of Immich’s internal upload tree
(`upload/<userId>/<xx>/<yy>/<uuid>.jpg`). Each album becomes a normal folder (e.g. `Holidays 2024/`), with the
original files inside, so you can browse and back them up easily via SMB/NFS without the hashed/segmented layout.

Includes a simple **web UI (Ingress)** to view progress and pushes **sensors** into Home Assistant so you can
build dashboards and automations.

---

## What it does
- Reads albums and asset paths from the Immich database.
- Resolves Immich’s internal paths (e.g., `upload/upload/...` or `/usr/src/app/upload/...`) to your mounted library.
- Copies files into **sanitized, per-album folders** under `export_dir` (no hashed subfolders).
- Skips or updates files by size and can clean up removed items with a deletion guard to prevent mass-deletes if the source is missing.


---

## Install

1. In Home Assistant: **Settings>> Add-ons>>Add-on Store>>Repositories>>Add**
2. Paste this repository URL and click **Add**:

```
https://github.com/frostaholic/immich_backup
```

3. Install **Immich Album Export**, start it, and (optionally) enable **Start on boot** and **Watchdog**.

> **Note:** Mount your Immich uploads share in HA first: **Settings>>System>>Storage>>Add network storage** (SMB/NFS).
> The mounted path should contain the Immich `upload/` tree.

---

## Configuration

Example:
```yaml
export_dir: "/media/immich"            # Where to write the exported albums
assets_root: "/media/immich_assets"    # Mounted library that contains 'upload/'
db_host: "10.0.0.5"                    # Immich Postgres host/IP
db_name: "immich"
db_user: "postgres"
db_pass: "YOUR_DB_PASSWORD"
immich_user_id: ""                     # Optional: limit to one user by UUID
schedule: "0 2 * * *"                  # Daily at 02:00
log_level: "info"                      # debug | info | warning | error

# Optional deletion guard (defaults shown)
min_found_abs: 100
min_found_fraction: 0.05
```

### What is `assets_root`?
Point this at the mount **inside HA** that contains your Immich files, e.g.:
```
/media/immich_assets/upload/...
```
The add-on automatically maps Immich DB paths like:
- `/usr/src/app/upload/...`
- `upload/upload/...`
- `upload/...`
to your `assets_root` so files can be copied.

### What is `export_dir`?
Where the add-on writes your album folders and `progress.json` (e.g., `/media/immich`).

---

## Web UI (Ingress)
- Open the add-on, click **OPEN WEB UI**.
- Shows live progress read from `progress.json`.

---

## Sensors created
The add-on publishes entities via the Supervisor API:

- `sensor.immich_backup_status`
- `binary_sensor.immich_backup_running`
- `sensor.immich_backup_copied`
- `sensor.immich_backup_skipped`
- `sensor.immich_backup_failed`
- `sensor.immich_backup_deleted`
- `sensor.immich_backup_total`
- `sensor.immich_backup_last_run`
- `sensor.immich_backup_guard`
- `sensor.immich_backup_error`
- **`sensor.immich_backup_percent_copied`** *(overall progress = (copied+skipped+failed)/total)*

Use these directly in dashboards and automationsâ€”no extra template sensors required.

---

## Example Dashboard

![Immich Backup Dashboard](docs/Screenshot_20250808_213710_Home%20Assistant.jpg)

Requires HACS with Mushroom, stack-in-card and multiple-entity-row installed.

---

## Safety: Deletion Guard
Cleanup (deleting files that no longer exist in Immich) is **skipped** when:
- The `assets_root` is unavailable/empty, or
- 0 source files were found, or
- Fewer than `min_found_abs` **and** below `min_found_fraction` of expected.

The guard reason is exposed via `sensor.immich_backup_guard`.

---

## Troubleshooting

**Skipping missing asset /usr/src/app/upload/**  
Your `assets_root` is wrong or doesnt contain `upload/`. Fix the mount or path.

**DB errors (relation/table not found)**  
Verify DB host/user/pass and network reachability from HA to the Immich PostgreSQL instance.

**No GUI button**  
Ensure the add-on includes Ingress and Flask is listening on `0.0.0.0:5000` (the add-on does this by default).

---
## ⚠️ Disclaimer

This add-on is **not** a primary backup solution. It copies Immich assets into
human-readable album folders and can remove files that no longer exist in
Immich. While there’s a *deletion guard* to prevent mass deletes (e.g., when
the source share is missing), it is not foolproof.

**Use real backups** (snapshots, versioned NAS, cloud, etc.). Follow 3-2-1:
three copies, two different media, one off-site.

---

This project is not affiliated with or endorsed by Immich or FUTO.
