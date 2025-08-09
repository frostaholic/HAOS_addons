import os
import shutil
import psycopg2
import json
import traceback
import urllib.request, urllib.error
import time
from datetime import datetime

# -------- helpers for safe env parsing --------
def _env_str(name, default=""):
    s = os.environ.get(name)
    if s is None or s == "" or s.lower() == "null" or s == "None":
        return default
    return s

def _env_int(name, default):
    s = os.environ.get(name)
    if s is None or s == "" or str(s).lower() == "null":
        return default
    try:
        return int(s)
    except Exception:
        return default

def _env_float(name, default):
    s = os.environ.get(name)
    if s is None or s == "" or str(s).lower() == "null":
        return default
    try:
        return float(s)
    except Exception:
        return default

def _pct(n, d):
    try:
        return round((float(n) * 100.0) / float(d), 1) if d else 0.0
    except Exception:
        return 0.0

# --- Config from env ---
EXPORT_DIR    = _env_str("EXPORT_DIR", "/mnt/album_export")
PROGRESS_FILE = os.path.join(EXPORT_DIR, "progress.json")
DB_HOST       = _env_str("DB_HOST", "localhost")
DB_PORT       = _env_int("DB_PORT", 5432)
DB_NAME       = _env_str("DB_NAME", "immich")
DB_USER       = _env_str("DB_USER", "postgres")
DB_PASS       = _env_str("DB_PASS", "password")
USER_ID       = _env_str("IMMICH_USER_ID", "")
ASSETS_ROOT   = _env_str("ASSETS_ROOT", "")  # e.g. "/media/immich_assets"

# Deletion guard thresholds
MIN_FOUND_ABS       = _env_int("MIN_FOUND_ABS", 100)          # min files found to allow deletion
MIN_FOUND_FRACTION  = _env_float("MIN_FOUND_FRACTION", 0.05)  # or min fraction of total

# Throttle for HA pushes (seconds)
HA_PUSH_INTERVAL_SEC = _env_int("HA_PUSH_INTERVAL_SEC", 60)

# Home Assistant Supervisor API (auto-injected token)
HA_API_BASE = "http://supervisor/core/api"
HA_TOKEN    = os.environ.get("SUPERVISOR_TOKEN")

progress = {
    "status":  "starting",
    "copied":  0,
    "skipped": 0,
    "failed":  0,
    "deleted": 0,
    "total":   0,
    "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
}

# ---------- HA helper ----------
def ha_post_state(entity_id: str, state, attributes: dict | None = None):
    """Create/update an entity state in Home Assistant. No-op if token missing."""
    if not HA_TOKEN:
        return
    try:
        url = f"{HA_API_BASE}/states/{entity_id}"
        body = json.dumps({"state": str(state), "attributes": attributes or {}}).encode()
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {HA_TOKEN}",
                     "Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        print(f"HA state push failed for {entity_id}: {e}")

def push_progress_to_ha():
    """Publish sensors to HA."""
    attrs = {"friendly_name": "Immich Backup", "icon": "mdi:cloud-sync"}
    ha_post_state("sensor.immich_backup_status",
                  progress.get("status", "unknown"), {**attrs})

    ha_post_state("binary_sensor.immich_backup_running",
                  "on" if progress.get("status") == "running" else "off",
                  {"friendly_name": "Immich Backup Running"})

    ha_post_state("sensor.immich_backup_copied",
                  progress.get("copied", 0),
                  {**attrs, "unit_of_measurement": "files", "icon": "mdi:file-upload"})
    ha_post_state("sensor.immich_backup_skipped",
                  progress.get("skipped", 0),
                  {**attrs, "unit_of_measurement": "files", "icon": "mdi:file-cancel-outline"})
    ha_post_state("sensor.immich_backup_failed",
                  progress.get("failed", 0),
                  {**attrs, "unit_of_measurement": "files", "icon": "mdi:alert-circle"})
    ha_post_state("sensor.immich_backup_deleted",
                  progress.get("deleted", 0),
                  {**attrs, "unit_of_measurement": "files", "icon": "mdi:trash-can-outline"})
    ha_post_state("sensor.immich_backup_total",
                  progress.get("total", 0),
                  {**attrs, "unit_of_measurement": "files", "icon": "mdi:counter"})
    ha_post_state("sensor.immich_backup_last_run",
                  progress.get("last_run", ""),
                  {**attrs, "icon": "mdi:clock-outline"})

    # Overall % done = (copied + skipped + failed) / total
    total = progress.get("total", 0) or 0
    processed = (
        (progress.get("copied", 0) or 0)
        + (progress.get("skipped", 0) or 0)
        + (progress.get("failed", 0) or 0)
    )
    ha_post_state(
        "sensor.immich_backup_percent_copied",
        _pct(processed, total),
        {"friendly_name": "Immich Backup % Copied", "unit_of_measurement": "%", "icon": "mdi:progress-check"}
    )

    ha_post_state("sensor.immich_backup_guard",
                  progress.get("guard", "") or "",
                  {**attrs, "icon": "mdi:shield-lock"})

    ha_post_state("sensor.immich_backup_error",
                  progress.get("error", "") or "",
                  {**attrs, "icon": "mdi:alert"})

# ---------- Throttle + dedupe for HA pushes ----------
_last_push_ts = 0
_last_pushed = {}

def _changed(new: dict) -> bool:
    global _last_pushed
    if not _last_pushed:
        _last_pushed = dict(new)
        return True
    for k, v in new.items():
        if _last_pushed.get(k) != v:
            _last_pushed = dict(new)
            return True
    return False

def maybe_push_progress_to_ha():
    """Throttle + de-duplicate HA sensor updates to avoid hammering HA."""
    global _last_push_ts
    now = time.time()
    snapshot = {
        "status":   progress.get("status"),
        "copied":   progress.get("copied", 0) or 0,
        "skipped":  progress.get("skipped", 0) or 0,
        "failed":   progress.get("failed", 0) or 0,
        "deleted":  progress.get("deleted", 0) or 0,
        "total":    progress.get("total", 0) or 0,
        "guard":    progress.get("guard", "") or "",
        "error":    progress.get("error", "") or "",
        "last_run": progress.get("last_run", "") or "",
    }
    if now - _last_push_ts < HA_PUSH_INTERVAL_SEC and not _changed(snapshot):
        return
    _last_push_ts = now
    push_progress_to_ha()

# ---------- DB helpers: auto-detect tables/columns (handles v1.137 changes) ----------
def connect_db():
    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        database=DB_NAME, user=DB_USER, password=DB_PASS
    )
    conn.autocommit = True
    return conn

def _columns_for_table(cur, table_name: str):
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s
    """, (table_name,))
    return {r[0] for r in cur.fetchall()}

def _first_existing_table(cur, names):
    for name in names:
        cur.execute("SELECT to_regclass(%s)", (f"public.{name}",))
        if cur.fetchone()[0]:
            return name
    return None

def _first_in(options, available: set[str]):
    for o in options:
        if o in available:
            return o
    return None

def get_albums_and_assets(conn):
    where = ""
    params = ()
    with conn.cursor() as cur:
        # Prefer singular on new versions, fall back to plural
        albums_table = _first_existing_table(cur, ["album", "albums"])
        assets_table = _first_existing_table(cur, ["asset", "assets"])
        if not albums_table or not assets_table:
            raise RuntimeError("Could not find album/asset tables in schema 'public'")

        # Detect join table by common name patterns
        join_table = _first_existing_table(
            cur,
            ["album_asset", "album_assets", "albums_assets", "albums_assets_assets", "album_assets_asset"]
        )
        if not join_table:
            # as a last resort, scan for any table containing both 'album' and 'asset'
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_name ILIKE '%album%' AND table_name ILIKE '%asset%'
            """)
            rows = [r[0] for r in cur.fetchall()]
            if rows:
                join_table = rows[0]
            else:
                raise RuntimeError("Could not find album↔asset join table in schema 'public'")

        # figure out FK column names on join table
        jcols = _columns_for_table(cur, join_table)
        album_fk = _first_in(["albumId", "albumsId"], jcols)
        asset_fk = _first_in(["assetId", "assetsId"], jcols)
        if not album_fk or not asset_fk:
            raise RuntimeError(f"Join table '{join_table}' missing album/asset FK columns")

        # album name column can be 'albumName' (old) or 'name' (new)
        acols = _columns_for_table(cur, albums_table)
        album_name_col = _first_in(["albumName", "name", "title"], acols) or "name"
        owner_col = "ownerId" if "ownerId" in acols else None

        # asset path column usually 'originalPath'
        scols = _columns_for_table(cur, assets_table)
        asset_path_col = _first_in(["originalPath", "original_path", "originalFilePath", "fileOriginalPath"], scols) or "originalPath"

        # optional user filter if owner column exists and USER_ID provided
        if USER_ID and owner_col:
            where = f'WHERE a."{owner_col}" = %s'
            params = (USER_ID,)

        q = f'''
            SELECT a.id, a."{album_name_col}", s."{asset_path_col}"
            FROM "{albums_table}" a
            JOIN "{join_table}" aa ON a.id = aa."{album_fk}"
            JOIN "{assets_table}" s ON aa."{asset_fk}" = s.id
            {where}
            ORDER BY a."{album_name_col}", s."{asset_path_col}"
        '''
        print(f"Using tables: {albums_table}, {assets_table}, join {join_table} ({album_fk}->{asset_fk})")
        cur.execute(q, params)
        return cur.fetchall()

# ---------- Core ----------
def write_progress():
    try:
        progress["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)
    except Exception as e:
        print(f"Failed to write progress: {e}")
    # Throttled, de-duplicated push
    maybe_push_progress_to_ha()

def fail(e, label):
    print(f"{label}: {e}")
    progress["status"] = "failed"
    progress["error"] = f"{label}: {e}\n"
    progress["traceback"] = traceback.format_exc()
    write_progress()

def sanitize(name):
    return "".join(c for c in name if c.isalnum() or c in (" ", "_", "-")).strip()

def assets_root_available() -> bool:
    """Heuristic: source root exists and is not empty (prevents delete when unmounted)."""
    if not ASSETS_ROOT:
        return True
    try:
        if not os.path.isdir(ASSETS_ROOT):
            return False
        with os.scandir(ASSETS_ROOT) as it:
            for _ in it:
                return True
        return False
    except Exception:
        return False

def translate_path(orig_path: str) -> str:
    """
    Map Immich DB paths to the mounted library (ASSETS_ROOT).
    Handles:
      - absolute container paths like /usr/src/app/upload/... or /usr/src/app/upload/upload/...
      - bare 'upload/...' and 'upload/upload/...'
    Your library root is expected to be: /media/immich_assets/upload/...
    """
    if not orig_path:
        return orig_path
    if os.path.isfile(orig_path):
        return orig_path
    if not ASSETS_ROOT:
        return orig_path

    rel = orig_path.replace("\\", "/").lstrip("/")
    if rel.startswith("usr/src/app/"):
        rel = rel[len("usr/src/app/"):]

    candidates = [
        os.path.join(ASSETS_ROOT, rel),
    ]
    if rel.startswith("upload/upload/"):
        candidates.append(os.path.join(ASSETS_ROOT, rel[len("upload/upload/"):]))
    if rel.startswith("upload/"):
        candidates.append(os.path.join(ASSETS_ROOT, rel[len("upload/"):]))

    for c in candidates:
        if os.path.isfile(c):
            return c
    return orig_path  # will be treated as missing

def get_existing_files_map():
    file_map = {}
    exts = {
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp",
        ".raw", ".dng", ".cr2", ".nef", ".arw", ".orf", ".rw2",
        ".pef", ".x3f", ".srw", ".raf", ".3fr", ".fff", ".iiq",
        ".k25", ".kdc", ".mos", ".mef", ".nrw", ".ptx", ".pxn",
        ".r3d", ".rwl", ".rwz", ".mp4", ".mov", ".avi",
        ".mkv", ".m4v", ".3gp", ".webm"
    }
    if not os.path.exists(EXPORT_DIR):
        return file_map
    for root, _, files in os.walk(EXPORT_DIR):
        for f in files:
            if f in {"progress.json", ".DS_Store", "Thumbs.db"}:
                continue
            if os.path.splitext(f)[1].lower() in exts:
                file_map[os.path.join(root, f)] = False
    return file_map

def copy_assets(albums_assets):
    progress.update({"status": "running", "copied": 0, "skipped": 0, "failed": 0, "deleted": 0})
    progress["total"] = len(albums_assets)
    write_progress()

    existing_map = get_existing_files_map()
    total = progress["total"]
    found_on_disk = 0  # count of assets that actually existed at source

    for i, (_, album_name, orig_path) in enumerate(albums_assets, start=1):
        asset_path = translate_path(orig_path)
        if not asset_path or not os.path.isfile(asset_path):
            print(f"Skipping missing asset: {asset_path}")
            progress["skipped"] += 1
            if i % 10 == 0 or i == total:
                write_progress()
            continue

        found_on_disk += 1
        safe = sanitize(album_name)
        dest_dir = os.path.join(EXPORT_DIR, safe)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, os.path.basename(asset_path))

        if dest_path in existing_map:
            existing_map[dest_path] = True

        if os.path.exists(dest_path):
            try:
                if os.path.getsize(asset_path) == os.path.getsize(dest_path):
                    print(f"Already exists, skipping: {dest_path}")
                    progress["skipped"] += 1
                else:
                    shutil.copy2(asset_path, dest_path)
                    print(f"Updated: {asset_path} -> {dest_path}")
                    progress["copied"] += 1
            except Exception as e:
                print(f"Error with {dest_path}: {e}")
                progress["failed"] += 1
        else:
            try:
                shutil.copy2(asset_path, dest_path)
                print(f"Copied: {asset_path} -> {dest_path}")
                progress["copied"] += 1
            except Exception as e:
                print(f"Failed copy {asset_path}: {e}")
                progress["failed"] += 1

        if i % 10 == 0 or i == total:
            write_progress()

    # --- DELETION GUARD ---
    guard_reason = None
    if not assets_root_available():
        guard_reason = "assets_root_unavailable_or_empty"
    elif found_on_disk == 0:
        guard_reason = "no_source_files_found"
    else:
        found_fraction = found_on_disk / max(1, total)
        if found_on_disk < MIN_FOUND_ABS and found_fraction < MIN_FOUND_FRACTION:
            guard_reason = (
                f"too_few_sources_found ({found_on_disk}<{MIN_FOUND_ABS} "
                f"and {found_fraction:.2%}<{MIN_FOUND_FRACTION:.0%})"
            )

    if guard_reason:
        print(f"Deletion guard triggered: {guard_reason}. Skipping cleanup deletions.")
        progress["guard"] = guard_reason
        progress["status"] = "complete"
        write_progress()
        return

    # Clean up files no longer present in Immich
    for path, keep in existing_map.items():
        if not keep:
            try:
                os.remove(path)
                print(f"Deleted: {path}")
                progress["deleted"] += 1
                parent = os.path.dirname(path)
                if parent != EXPORT_DIR and not os.listdir(parent):
                    os.rmdir(parent)
                    print(f"Removed empty dir: {parent}")
            except Exception as e:
                print(f"Failed delete {path}: {e}")

    progress["status"] = "complete"
    write_progress()

def main():
    try:
        print(f"Connecting to database {DB_HOST}:{DB_PORT} …")
        conn = connect_db()
        # quick probe
        with conn.cursor() as cur:
            cur.execute("SET search_path TO public;")
            cur.execute("SELECT 1;")
        print("DB OK")

        print("Fetching albums and assets...")
        assets = get_albums_and_assets(conn)
        print(f"Found {len(assets)} assets in DB")

        if assets:
            copy_assets(assets)
        else:
            print("No assets found to export")
            progress["status"] = "complete"
            write_progress()

        conn.close()
        print("Export completed successfully")

    except psycopg2.Error as e:
        fail(e, "Database error")
    except Exception as e:
        fail(e, "Unexpected error")

if __name__ == "__main__":
    main()

