#!/usr/bin/env python3
import os, json, time, threading, subprocess
from flask import Flask, jsonify, request, Response

ADDON_VERSION = os.environ.get("ADDON_VERSION", "unknown")

# --- paths ---
EXPORT_SCRIPT = "/usr/src/app/export_immich_albums_db.py"
EXPORT_DIR    = os.environ.get("EXPORT_DIR", "/mnt/album_export")
PROGRESS_FILE = os.path.join(EXPORT_DIR, "progress.json")
LOCK_FILE     = "/tmp/immich_export.lock"
RUN_LOG       = "/tmp/immich_run.log"
WEB_LOG       = "/tmp/immich_webgui.log"

app = Flask(__name__)
_last_progress = None

# --- helpers ---
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(WEB_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass

def read_progress():
    """Return progress; tolerate concurrent writes by retrying and caching last good."""
    global _last_progress
    for _ in range(3):
        try:
            with open(PROGRESS_FILE, "r") as f:
                data = json.load(f)
            _last_progress = data
            return data
        except Exception:
            time.sleep(0.05)  # tiny backoff while exporter is swapping files
    # if we still can't read, return last good to avoid UI flicker
    return _last_progress or {
        "status": "unknown", "copied": 0, "skipped": 0, "failed": 0,
        "deleted": 0, "total": 0, "last_run": ""
    }


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def is_running() -> bool:
    if not os.path.exists(LOCK_FILE):
        return False
    try:
        with open(LOCK_FILE, "r") as f:
            pid = int(f.read().strip())
        return pid_alive(pid)
    except Exception:
        return False

def clear_stale_lock():
    if not os.path.exists(LOCK_FILE):
        return
    try:
        with open(LOCK_FILE, "r") as f:
            pid = int(f.read().strip())
        if not pid_alive(pid):
            os.remove(LOCK_FILE)
            log(f"Cleared stale lock (pid {pid}).")
    except Exception:
        try: os.remove(LOCK_FILE)
        except Exception: pass

def run_export_background():
    env = os.environ.copy()
    log("Starting export subprocess…")
    with open(RUN_LOG, "a") as lf:
        lf.write("\n===== START EXPORT {} =====\n".format(time.strftime("%Y-%m-%d %H:%M:%S")))
        lf.flush()

    proc = subprocess.Popen(
        ["python3", EXPORT_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        bufsize=1
    )

    try:
        with open(LOCK_FILE, "w") as f:
            f.write(str(proc.pid))
    except Exception as e:
        log(f"Failed to write lock file: {e}")

    try:
        with open(RUN_LOG, "a") as lf:
            for line in proc.stdout:
                lf.write(line); lf.flush()
                print(line, end="", flush=True)
    except Exception as e:
        log(f"Error capturing exporter output: {e}")

    rc = proc.wait()
    log(f"Export subprocess finished with rc={rc}")
    try:
        if os.path.exists(LOCK_FILE): os.remove(LOCK_FILE)
    except Exception:
        pass

# --- routes ---
@app.route("/")
def index():
    html = """<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Immich Album Export - Enhanced</title>
<style>
  :root {
    --primary: #0066cc; --primary-hover: #0052a3; --success: #22c55e; --warning: #f59e0b; --error: #ef4444;
    --bg: #f8fafc; --card: #ffffff; --text: #1e293b; --text-muted: #64748b; --border: #e2e8f0;
    --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
  }
  
  * { box-sizing: border-box; }
  
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    background: var(--bg); color: var(--text); margin: 0; padding: 24px; line-height: 1.6;
    min-height: 100vh;
  }
  
  .container { max-width: 1200px; margin: 0 auto; }
  
  .header {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px;
    padding-bottom: 24px; border-bottom: 2px solid var(--border);
  }
  
  .title { font-size: 2.25rem; font-weight: 800; color: var(--text); margin: 0; }
  
  .version-badge {
    background: linear-gradient(135deg, var(--primary), #3b82f6);
    color: white; padding: 8px 16px; border-radius: 24px; font-weight: 600;
    font-size: 0.875rem; box-shadow: var(--shadow);
  }
  
  .controls {
    display: flex; gap: 16px; margin-bottom: 32px; flex-wrap: wrap; align-items: center;
  }
  
  .btn {
    padding: 12px 24px; border: none; border-radius: 12px; font-weight: 600; cursor: pointer;
    font-size: 1rem; transition: all 0.2s ease; display: inline-flex; align-items: center; gap: 8px;
    box-shadow: var(--shadow);
  }
  
  .btn-primary {
    background: linear-gradient(135deg, var(--primary), #3b82f6); color: white;
  }
  .btn-primary:hover:not(:disabled) {
    background: linear-gradient(135deg, var(--primary-hover), #2563eb);
    transform: translateY(-2px); box-shadow: 0 8px 15px -3px rgba(0, 0, 0, 0.1);
  }
  
  .btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none !important; }
  
  .status-badge {
    padding: 8px 16px; border-radius: 24px; font-weight: 600; font-size: 0.9rem;
    display: inline-flex; align-items: center; gap: 8px; box-shadow: var(--shadow);
  }
  
  .status-running { background: #dcfce7; color: #166534; }
  .status-complete { background: #dbeafe; color: #1e40af; }
  .status-failed { background: #fef2f2; color: #b91c1c; }
  
  .cards {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 24px; margin-bottom: 32px;
  }
  
  .card {
    background: var(--card); border-radius: 16px; padding: 24px; box-shadow: var(--shadow);
    border: 1px solid var(--border); transition: transform 0.2s ease;
  }
  
  .card:hover { transform: translateY(-2px); }
  
  .card-title {
    font-size: 1.5rem; font-weight: 700; margin: 0 0 20px 0; color: var(--text);
    display: flex; align-items: center; gap: 8px;
  }
  
  .progress-container { margin: 20px 0; }
  
  .progress-bar {
    width: 100%; height: 12px; background: var(--border); border-radius: 8px;
    overflow: hidden; margin: 12px 0; box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.1);
  }
  
  .progress-fill {
    height: 100%; background: linear-gradient(90deg, var(--primary), var(--success));
    border-radius: 8px; transition: width 0.5s ease; position: relative;
  }
  
  .progress-fill::after {
    content: ''; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent);
    animation: shimmer 2s infinite;
  }
  
  @keyframes shimmer {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(100%); }
  }
  
  .progress-text {
    font-size: 1.1rem; font-weight: 600; text-align: center; margin-top: 8px;
  }
  
  .stats-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 16px; margin-top: 20px;
  }
  
  .stat-item {
    text-align: center; padding: 16px; background: var(--bg); border-radius: 12px;
    border: 1px solid var(--border);
  }
  
  .stat-value {
    font-size: 1.75rem; font-weight: 800; color: var(--primary); margin-bottom: 4px;
  }
  
  .stat-label {
    font-size: 0.875rem; color: var(--text-muted); text-transform: uppercase;
    letter-spacing: 0.5px; font-weight: 500;
  }
  
  .info-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px 20px; margin-top: 20px;
  }
  
  .info-label { color: var(--text-muted); font-size: 0.9rem; }
  .info-value { font-weight: 700; font-size: 1rem; }
  
  .ha-fix { color: var(--success); font-weight: 700; }
  
  .log-container {
    background: #1a1b23; color: #e2e8f0; border-radius: 12px; padding: 20px;
    font-family: 'SF Mono', Monaco, 'Cascadia Code', 'Roboto Mono', Consolas, monospace;
    font-size: 0.875rem; line-height: 1.5; max-height: 400px; overflow-y: auto;
    box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.2);
  }
  
  .log-container::-webkit-scrollbar { width: 8px; }
  .log-container::-webkit-scrollbar-track { background: #374151; border-radius: 4px; }
  .log-container::-webkit-scrollbar-thumb { background: #6b7280; border-radius: 4px; }
  
  .two-column {
    display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 24px;
  }
  
  @media (max-width: 768px) {
    .two-column { grid-template-columns: 1fr; }
    .title { font-size: 1.875rem; }
    .cards { grid-template-columns: 1fr; }
  }
  
  .icon { width: 20px; height: 20px; }
</style>
</head>
<body>

<div class="container">
  <div class="header">
    <h1 class="title">Immich Album Export</h1>
    <div class="version-badge">v""" + ADDON_VERSION + """</div>
  </div>
  
  <div class="controls">
    <button class="btn btn-primary" id="runBtn">
      <span class="icon">▶️</span>
      Run Export
    </button>
    <span class="status-badge status-complete" id="statusBadge">
      Status: Loading...
    </span>
  </div>

  <div class="cards">
    <div class="card">
      <h2 class="card-title">📊 Progress</h2>
      <div class="progress-container">
        <div class="progress-bar">
          <div class="progress-fill" id="progressBar" style="width: 0%"></div>
        </div>
        <div class="progress-text" id="progressText">0% Complete (0 of 0 files)</div>
      </div>
      
      <div class="stats-grid">
        <div class="stat-item">
          <div class="stat-value" id="copied">0</div>
          <div class="stat-label">Copied</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" id="skipped">0</div>
          <div class="stat-label">Skipped</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" id="failed">0</div>
          <div class="stat-label">Failed</div>
        </div>
        <div class="stat-item">
          <div class="stat-value" id="deleted">0</div>
          <div class="stat-label">Deleted</div>
        </div>
      </div>
    </div>
    
    <div class="card">
      <h2 class="card-title">⚙️ System Status</h2>
      <div class="info-grid">
        <div class="info-label">Last Run</div>
        <div class="info-value" id="lastRun">Never</div>
        
        <div class="info-label">Running</div>
        <div class="info-value" id="running">No</div>
        
        <div class="info-label">Export Directory</div>
        <div class="info-value" id="exportDir">""" + EXPORT_DIR + """</div>
        
        <div class="info-label">HA Push Interval</div>
        <div class="info-value ha-fix">""" + os.environ.get('HA_PUSH_INTERVAL_SEC', '60') + """s ✅</div>
        
        <div class="info-label">Guard Status</div>
        <div class="info-value" id="guard">—</div>
        
        <div class="info-label">Error Status</div>
        <div class="info-value" id="error">—</div>
      </div>
    </div>
  </div>
  
  <div class="two-column">
    <div class="card">
      <h3 class="card-title">📋 Live Log</h3>
      <div class="log-container" id="logContainer">(Loading...)</div>
    </div>
    <div class="card">
      <h3 class="card-title">🔧 Raw Progress Data</h3>
      <div class="log-container" id="rawData">(Loading...)</div>
    </div>
  </div>
</div>

<script>
const elements = {
  runBtn: document.getElementById('runBtn'),
  statusBadge: document.getElementById('statusBadge'),
  progressBar: document.getElementById('progressBar'),
  progressText: document.getElementById('progressText'),
  copied: document.getElementById('copied'),
  skipped: document.getElementById('skipped'),
  failed: document.getElementById('failed'),
  deleted: document.getElementById('deleted'),
  lastRun: document.getElementById('lastRun'),
  running: document.getElementById('running'),
  exportDir: document.getElementById('exportDir'),
  guard: document.getElementById('guard'),
  error: document.getElementById('error'),
  logContainer: document.getElementById('logContainer'),
  rawData: document.getElementById('rawData')
};

// Build URLs relative to current path (works with HA Ingress)
const BASE = location.pathname.replace(/\/$/, '');
const apiUrl = (p) => `${BASE}/${p.replace(/^\//,'')}`;

function updateUI(status) {
  const p = status.progress || {};
  const running = status.running;
  elements.runBtn.disabled = running;
  elements.runBtn.innerHTML = running ? 
    '<span class="icon">⏸️</span> Running...' : 
    '<span class="icon">▶️</span> Run Export';

  let statusClass = 'status-complete';
  let statusText = p.status || 'unknown';
  if (running) { statusClass = 'status-running'; statusText = 'Running'; }
  else if (p.status === 'failed') { statusClass = 'status-failed'; statusText = 'Failed'; }
  elements.statusBadge.className = 'status-badge ' + statusClass;
  elements.statusBadge.textContent = 'Status: ' + statusText;

  const total = p.total || 0;
  const processed = (p.copied || 0) + (p.skipped || 0) + (p.failed || 0);
  const percentage = total > 0 ? Math.round((processed / total) * 100) : 0;
  elements.progressBar.style.width = percentage + '%';
  elements.progressText.textContent =
    percentage + '% Complete (' + processed.toLocaleString() + ' of ' + total.toLocaleString() + ' files)';

  elements.copied.textContent = (p.copied || 0).toLocaleString();
  elements.skipped.textContent = (p.skipped || 0).toLocaleString();
  elements.failed.textContent = (p.failed || 0).toLocaleString();
  elements.deleted.textContent = (p.deleted || 0).toLocaleString();

  elements.lastRun.textContent = p.last_run || 'Never';
  elements.running.textContent = running ? 'Yes' : 'No';
  elements.guard.textContent = p.guard || '—';
  elements.error.textContent = p.error || '—';
  elements.rawData.textContent = JSON.stringify(p, null, 2);
}

async function apiCall(path, options) {
  const url = apiUrl(path);
  try {
    const r = await fetch(url, options);
    let data;
    try { data = await r.json(); }
    catch { data = { ok: false, error: await r.text() }; }
    if (!r.ok) data.ok = false;
    return data;
  } catch (error) {
    return { ok: false, error: String(error) };
  }
}

async function refreshData() {
  try {
    const [status, logResponse] = await Promise.all([
      apiCall('status'),
      fetch(apiUrl('log?tail=200'))
    ]);
    updateUI(status);
    const logText = await logResponse.text();
    elements.logContainer.textContent = logText;
    elements.logContainer.scrollTop = elements.logContainer.scrollHeight;
  } catch (error) {
    console.error('Refresh failed:', error);
  }
}

elements.runBtn.onclick = async () => {
  const result = await apiCall('run-now', { method: 'POST' });
  if (result.ok) {
    setTimeout(refreshData, 1000);
  } else {
    alert('Failed to start export: ' + (result.reason || result.error || 'Unknown error'));
  }
};

setInterval(refreshData, 3000);
refreshData();
</script>

</body>
</html>"""
    return Response(html, mimetype="text/html")

@app.route("/status")
def status():
    clear_stale_lock()
    p = read_progress()
    try: p.setdefault("export_dir", EXPORT_DIR)
    except Exception: pass
    return jsonify({
        "running": is_running(),
        "progress": p,
        "lock_file": os.path.exists(LOCK_FILE),
        "run_log": RUN_LOG,
    })

@app.route("/run-now", methods=["POST","GET"])
def run_now():
    clear_stale_lock()
    if is_running():
        return Response(json.dumps({"ok":False,"reason":"already_running"},separators=(",",":")),
                        mimetype="application/json")
    t = threading.Thread(target=run_export_background, daemon=True)
    t.start()
    log("Manual run requested from UI.")
    return Response(json.dumps({"ok":True,"started":True},separators=(",",":")),
                    mimetype="application/json")

# optional alias to handle trailing slash
@app.route("/run-now/", methods=["POST","GET"])
def run_now_slash():
    return run_now()

@app.route("/log")
def log_tail():
    n = 200
    try:
        n = int(request.args.get("tail","200"))
    except Exception:
        pass
    try:
        with open(RUN_LOG, "rb") as f:
            data = f.read()
        lines = data.decode(errors="replace").splitlines()[-n:]
        return Response("\n".join(lines), mimetype="text/plain")
    except FileNotFoundError:
        return Response("(no run log yet)", mimetype="text/plain")

@app.route("/progress")
def progress_json():
    return jsonify(read_progress())

if __name__ == "__main__":
    log("Enhanced Web GUI starting on 0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)

