# webgui.py
from flask import Flask, jsonify, render_template_string
import os, json

app = Flask(__name__)

# Make sure this matches EXPORT_DIR in your run.sh / Python script
EXPORT_DIR = os.environ["EXPORT_DIR"]
PROGRESS   = os.path.join(EXPORT_DIR, "progress.json")

TEMPLATE = """
<!doctype html>
<title>Backup Progress</title>
<h1>Backup Progress</h1>
<pre id=out>Loading…</pre>
<script>
async function load() {
  let r = await fetch('progress');
  if (!r.ok) {
    document.getElementById('out').textContent =
      `Error ${r.status}: ${await r.text()}`;
    return;
  }
  let j = await r.json();
  document.getElementById('out').textContent =
    JSON.stringify(j, null, 2);
}
setInterval(load, 5000);
load();
</script>
"""

@app.route("/")
def home():
    return render_template_string(TEMPLATE)

@app.route("/progress")
def prog():
    try:
        with open(PROGRESS) as f:
            data = json.load(f)
    except FileNotFoundError:
        return jsonify({"error": "progress file not found"}), 404
    except json.JSONDecodeError:
        return jsonify({"error": "invalid or empty progress file"}), 500
    return jsonify(data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

