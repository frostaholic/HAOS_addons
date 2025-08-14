import os
import shutil
import psycopg2
import json
import traceback
import urllib.request, urllib.error
import time
import logging
import signal
import unicodedata
from datetime import datetime

# Enhanced logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/tmp/immich_export_detailed.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

# Enhanced signal handling for pause/resume
pause_requested = False
shutdown_requested = False

def signal_handler(signum, frame):
    global pause_requested, shutdown_requested
    if signum == signal.SIGUSR1:
        logger.info("Pause requested")
        pause_requested = True
    elif signum == signal.SIGUSR2:
        logger.info("Resume requested")
        pause_requested = False
    elif signum in (signal.SIGTERM, signal.SIGINT):
        logger.info("Shutdown requested")
        shutdown_requested = True

signal.signal(signal.SIGUSR1, signal_handler)
signal.signal(signal.SIGUSR2, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

# -------- Your original helpers (unchanged) --------
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


EXPORT_DIR    = _env_str("EXPORT_DIR", "/mnt/album_export")
PROGRESS_FILE = os.path.join(EXPORT_DIR, "progress.json")
DB_HOST       = _env_str("DB_HOST", "localhost")
DB_PORT       = _env_int("DB_PORT", 5432)
DB_NAME       = _env_str("DB_NAME", "immich")
DB_USER       = _env_str("DB_USER", "postgres")
DB_PASS       = _env_str("DB_PASS", "password")
USER_ID       = _env_str("IMMICH_USER_ID", "")
ASSETS_ROOT   = _env_str("ASSETS_ROOT", "")

# Deletion guard thresholds
MIN_FOUND_ABS       = _env_int("MIN_FOUND_ABS", 100)
MIN_FOUND_FRACTION  = _env_float("MIN_FOUND_FRACTION", 0.05)

# Enhanced: Throttle for HA pushes (YOUR FIX)
HA_PUSH_INTERVAL_SEC = _env_int("HA_PUSH_INTERVAL_SEC", 60)

# Enhanced: Performance options
PARALLEL_COPIES = _env_int("PARALLEL_COPIES", 1)  # Default to 1 to maintain compatibility
INTEGRITY_CHECK = os.environ.get("SKIP_INTEGRITY_CHECK", "false").lower() != "true"

# Home Assistant Supervisor API 
HA_API_BASE = "http://supervisor/core/api"
HA_TOKEN    = os.environ.get("SUPERVISOR_TOKEN")

# Enhanced progress tracking
progress = {
    "status":  "starting",
    "copied":  0,
    "skipped": 0,
    "failed":  0,
    "deleted": 0,
    "total":   0,
    "current_album": "",
    "current_file": "",
    "files_per_second": 0,
    "estimated_remaining": None,
    "paused": False,
    "can_pause": False,
    "can_resume": False,
    "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
}

# ---------- Enhanced HA helper ----------
def ha_post_state(entity_id: str, state, attributes: dict | None = None):
    """Enhanced with better error handling and retries"""
    if not HA_TOKEN:
        return
    
    max_retries = 2
    for attempt in range(max_retries):
        try:
            url = f"{HA_API_BASE}/states/{entity_id}"
            body = json.dumps({"state": str(state), "attributes": attributes or {}}).encode()
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Authorization": f"Bearer {HA_TOKEN}",
                         "Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=5).read()
            return
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                logger.debug(f"HA state push failed for {entity_id}: {e}")

def push_progress_to_ha():
    """Enhanced with additional sensors"""
    attrs = {"friendly_name": "Immich Backup", "icon": "mdi:cloud-sync"}
    
    # Your original sensors
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

    # Enhanced: Additional sensors
    ha_post_state("binary_sensor.immich_backup_paused",
                  "on" if progress.get("paused", False) else "off",
                  {"friendly_name": "Immich Backup Paused"})
    
    ha_post_state("sensor.immich_backup_files_per_second",
                  progress.get("files_per_second", 0),
                  {**attrs, "unit_of_measurement": "files/s", "icon": "mdi:speedometer"})

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
        {"friendly_name": "Immich Backup % Complete", "unit_of_measurement": "%", "icon": "mdi:progress-check"}
    )

    ha_post_state("sensor.immich_backup_guard",
                  progress.get("guard", "") or "",
                  {**attrs, "icon": "mdi:shield-lock"})

    ha_post_state("sensor.immich_backup_error",
                  progress.get("error", "") or "",
                  {**attrs, "icon": "mdi:alert"})


_last_push_ts = 0
_last_pushed = {}
_last_status = None

def maybe_push_progress_to_ha():
    global _last_push_ts, _last_status
    now = time.time()

    status = progress.get("status")
    if status != _last_status:
        _last_status = status
        _last_push_ts = now
        push_progress_to_ha()
        return

    interval = max(1, int(os.environ.get("HA_PUSH_INTERVAL_SEC", "60")))
    if now - _last_push_ts >= interval:
        _last_push_ts = now
        push_progress_to_ha()

def connect_db():
    """Enhanced with retry logic"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = psycopg2.connect(
                host=DB_HOST, port=DB_PORT,
                database=DB_NAME, user=DB_USER, password=DB_PASS,
                connect_timeout=30
            )
            conn.autocommit = True
            logger.info(f"Database connected (attempt {attempt + 1})")
            return conn
        except psycopg2.Error as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.warning(f"DB connection failed, retrying in {wait_time}s: {e}")
                time.sleep(wait_time)
            else:
                raise

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
    """Your original function with enhanced error handling"""
    where = ""
    params = ()
    with conn.cursor() as cur:
        # Your original logic
        albums_table = _first_existing_table(cur, ["album", "albums"])
        assets_table = _first_existing_table(cur, ["asset", "assets"])
        if not albums_table or not assets_table:
            raise RuntimeError("Could not find album/asset tables in schema 'public'")

        join_table = _first_existing_table(
            cur,
            ["album_asset", "album_assets", "albums_assets", "albums_assets_assets", "album_assets_asset"]
        )
        if not join_table:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='public' AND table_name ILIKE '%album%' AND table_name ILIKE '%asset%'
            """)
            rows = [r[0] for r in cur.fetchall()]
            if rows:
                join_table = rows[0]
            else:
                raise RuntimeError("Could not find album↔asset join table in schema 'public'")

        jcols = _columns_for_table(cur, join_table)
        album_fk = _first_in(["albumId", "albumsId"], jcols)
        asset_fk = _first_in(["assetId", "assetsId"], jcols)
        if not album_fk or not asset_fk:
            raise RuntimeError(f"Join table '{join_table}' missing album/asset FK columns")

        acols = _columns_for_table(cur, albums_table)
        album_name_col = _first_in(["albumName", "name", "title"], acols) or "name"
        owner_col = "ownerId" if "ownerId" in acols else None

        scols = _columns_for_table(cur, assets_table)
        asset_path_col = _first_in(["originalPath", "original_path", "originalFilePath", "fileOriginalPath"], scols) or "originalPath"

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
        logger.info(f"Using tables: {albums_table}, {assets_table}, join {join_table} ({album_fk}->{asset_fk})")
        cur.execute(q, params)
        return cur.fetchall()

def write_progress():
    try:
        progress["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(os.path.dirname(PROGRESS_FILE), exist_ok=True)
        tmp = PROGRESS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(progress, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PROGRESS_FILE)  # atomic on same filesystem
    except Exception as e:
        logger.error(f"Failed to write progress: {e}")
    # push to HA (throttled)
    maybe_push_progress_to_ha()


def fail(e, label):
    logger.error(f"{label}: {e}")
    progress["status"] = "failed"
    progress["error"] = f"{label}: {e}\n"
    progress["traceback"] = traceback.format_exc()
    write_progress()

def sanitize(name):
    
    if not name:
        return "Unknown_Album"
    # normalize unicode so accents/variants behave consistently
    n = unicodedata.normalize("NFKD", name)
    # keep only letters/numbers/space/underscore/dash (no dots, no symbols)
    cleaned = "".join(c for c in n if c.isalnum() or c in (" ", "_", "-"))
    # collapse multiple spaces and trim
    cleaned = " ".join(cleaned.split()).strip()
    # optional: cap length to avoid crazy-long filenames
    return cleaned[:100] or "Unknown_Album"

def assets_root_available() -> bool:
    """Enhanced with better logging"""
    if not ASSETS_ROOT:
        logger.info("ASSETS_ROOT not configured, assuming direct paths")
        return True
    try:
        if not os.path.isdir(ASSETS_ROOT):
            logger.error(f"ASSETS_ROOT directory does not exist: {ASSETS_ROOT}")
            return False
        with os.scandir(ASSETS_ROOT) as it:
            for _ in it:
                return True
        logger.warning(f"ASSETS_ROOT directory is empty: {ASSETS_ROOT}")
        return False
    except Exception as e:
        logger.error(f"Error checking ASSETS_ROOT: {e}")
        return False

def translate_path(orig_path: str) -> str:
    """Your original function unchanged"""
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
    return orig_path

def get_existing_files_map():
    """Your original function with enhanced logging"""
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
    
    logger.info(f"Found {len(file_map)} existing files in export directory")
    return file_map

def copy_assets(albums_assets):
    """Enhanced with pause/resume and better progress tracking"""
    global pause_requested, shutdown_requested
    
    progress.update({
        "status": "running", 
        "copied": 0, "skipped": 0, "failed": 0, "deleted": 0,
        "can_pause": True, "can_resume": False
    })
    progress["total"] = len(albums_assets)
    write_progress()

    existing_map = get_existing_files_map()
    total = progress["total"]
    found_on_disk = 0
    start_time = time.time()

    for i, (_, album_name, orig_path) in enumerate(albums_assets, start=1):
        # Enhanced: Handle pause/resume
        while pause_requested and not shutdown_requested:
            progress["paused"] = True
            progress["can_resume"] = True
            progress["can_pause"] = False
            write_progress()
            time.sleep(1)
            
        if shutdown_requested:
            logger.info("Shutdown requested, stopping gracefully")
            break
            
        if progress.get("paused") and not pause_requested:
            progress["paused"] = False
            progress["can_pause"] = True
            progress["can_resume"] = False
        
        asset_path = translate_path(orig_path)
        if not asset_path or not os.path.isfile(asset_path):
            logger.debug(f"Skipping missing asset: {asset_path}")
            progress["skipped"] += 1
            if i % 10 == 0 or i == total:
                write_progress()
            continue

        found_on_disk += 1
        safe = sanitize(album_name)
        dest_dir = os.path.join(EXPORT_DIR, safe)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, os.path.basename(asset_path))

        # Enhanced: Track current processing
        progress["current_album"] = album_name
        progress["current_file"] = os.path.basename(asset_path)

        if dest_path in existing_map:
            existing_map[dest_path] = True

        if os.path.exists(dest_path):
            try:
                if os.path.getsize(asset_path) == os.path.getsize(dest_path):
                    logger.debug(f"Already exists, skipping: {dest_path}")
                    progress["skipped"] += 1
                else:
                    shutil.copy2(asset_path, dest_path)
                    logger.debug(f"Updated: {asset_path} -> {dest_path}")
                    progress["copied"] += 1
            except Exception as e:
                logger.error(f"Error with {dest_path}: {e}")
                progress["failed"] += 1
        else:
            try:
                shutil.copy2(asset_path, dest_path)
                logger.debug(f"Copied: {asset_path} -> {dest_path}")
                progress["copied"] += 1
            except Exception as e:
                logger.error(f"Failed copy {asset_path}: {e}")
                progress["failed"] += 1

        # Enhanced: Calculate performance stats
        elapsed = time.time() - start_time
        if elapsed > 0:
            processed = progress["copied"] + progress["skipped"] + progress["failed"]
            progress["files_per_second"] = round(processed / elapsed, 2)
            
            if processed < total:
                remaining = total - processed
                estimated_remaining = remaining / (processed / elapsed)
                progress["estimated_remaining"] = int(estimated_remaining)

        if i % 10 == 0 or i == total:
            write_progress()

    # Your original deletion guard logic
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
        logger.warning(f"Deletion guard triggered: {guard_reason}. Skipping cleanup deletions.")
        progress["guard"] = guard_reason
        progress["status"] = "complete"
        write_progress()
        return

    # Clean up files no longer present in Immich
    for path, keep in existing_map.items():
        if not keep:
            try:
                os.remove(path)
                logger.debug(f"Deleted: {path}")
                progress["deleted"] += 1
                parent = os.path.dirname(path)
                if parent != EXPORT_DIR and not os.listdir(parent):
                    os.rmdir(parent)
                    logger.debug(f"Removed empty dir: {parent}")
            except Exception as e:
                logger.error(f"Failed delete {path}: {e}")

    progress["status"] = "complete"
    progress["can_pause"] = False
    progress["can_resume"] = False
    write_progress()

def main():
    """Enhanced with better startup logging"""
    logger.info("Starting Immich Album Export (Enhanced)")
    logger.info(f"HA Push Interval: {HA_PUSH_INTERVAL_SEC}s (YOUR FIX APPLIED)")
    
    try:
        logger.info(f"Connecting to database {DB_HOST}:{DB_PORT}...")
        conn = connect_db()
        
        with conn.cursor() as cur:
            cur.execute("SET search_path TO public;")
            cur.execute("SELECT 1;")
        logger.info("Database connection verified")

        logger.info("Fetching albums and assets...")
        assets = get_albums_and_assets(conn)
        logger.info(f"Found {len(assets)} assets in DB")

        if assets:
            copy_assets(assets)
        else:
            logger.warning("No assets found to export")
            progress["status"] = "complete"
            write_progress()

        conn.close()
        logger.info("Export completed successfully")

    except psycopg2.Error as e:
        fail(e, "Database error")
    except Exception as e:
        fail(e, "Unexpected error")

if __name__ == "__main__":
    main()
