"""telek6 - a long-lived k6 runner behind telelocust's REST contract.

POST /runs/start        {script_base64, files?: {name: base64}, env?: {K: V},
                         args?: [..k6 args..], timeout_s?: int} -> 201 {token}
GET  /runs/<token>      {status: running|finished, exit_code, started_at, finished_at}
POST /runs/<token>/stop SIGINT k6 (graceful, k6 flushes its summary), SIGKILL after grace
GET  /runs/<token>/download  zip of the run directory: script.js, the files,
                         console.ndjson (the script's console.log lines), stdout.ndjson
                         (what handleSummary returns as stdout), stderr.log (k6's own log)
GET  /healthz            -> {status, k6: first line of `k6 version`}

Generic over what the script does: the plan is just a file, the summary is
whatever k6 prints. It runs arbitrary scripts sent to it - cluster-internal
only, no auth, same trust model as telelocust.

ponytail: stdlib only, in-memory run table (lost on restart; the run dirs on
disk survive). One k6 process at a time is the intended use; nothing stops two.
"""

import base64
import io
import json
import os
import shutil
import signal
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HTTP_PORT = int(os.environ.get("TELEK6_HTTP_PORT", "5124"))
DATA_PATH = os.environ.get("TELEK6_DATA", "./data")
K6 = os.environ.get("TELEK6_K6", "k6")
# First line of `k6 version`, read once at startup and served on /healthz, so a
# client can refuse a generator whose k6 is not the one it was tested against.
K6_VERSION = None
STOP_GRACE_S = float(os.environ.get("TELEK6_STOP_GRACE_S", "10"))

runs = {}       # token -> dict(status, exit_code, started_at, finished_at, timeout_s)
processes = {}  # token -> Popen
handles = {}    # token -> (stdout, stderr) files, closed when the run finishes
lock = threading.Lock()


def run_dir(token):
    return os.path.join(DATA_PATH, token)


def start_run(body):
    token = f"{time.time():.6f}"
    d = run_dir(token)
    os.makedirs(d, exist_ok=False)
    with open(os.path.join(d, "script.js"), "wb") as fh:
        fh.write(base64.b64decode(body["script_base64"]))
    for name, b64 in (body.get("files") or {}).items():
        if os.path.basename(name) != name:
            raise ValueError(f"file name {name!r} must be a bare name")
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(base64.b64decode(b64))
    # console.log goes to stderr by default; give it its own file so a script's
    # records never interleave with k6's own log lines.
    cmd = [K6, "run", "--log-format=raw", "--console-output=console.ndjson", *(body.get("args") or []), "script.js"]
    env = {**os.environ, **{str(k): str(v) for k, v in (body.get("env") or {}).items()}}
    out = open(os.path.join(d, "stdout.ndjson"), "wb")
    err = open(os.path.join(d, "stderr.log"), "wb")
    with lock:
        runs[token] = {"status": "running", "exit_code": None, "started_at": time.time(),
                       "finished_at": None, "timeout_s": body.get("timeout_s"), "cmd": cmd}
        processes[token] = subprocess.Popen(cmd, cwd=d, env=env, stdout=out, stderr=err)
        handles[token] = (out, err)
    if body.get("timeout_s"):
        threading.Timer(float(body["timeout_s"]), stop_run, args=(token, "timeout")).start()
    return token


def poll(token):
    r = runs[token]
    p = processes[token]
    code = p.poll()
    if code is not None and r["status"] == "running":
        r["status"], r["exit_code"], r["finished_at"] = "finished", code, time.time()
        for fh in handles.pop(token, ()):
            fh.close()
    return r


def stop_run(token, reason="stop"):
    r = poll(token)
    if r["status"] != "running":
        return r
    p = processes[token]
    p.send_signal(signal.SIGINT)      # k6 stops the test and still runs handleSummary
    try:
        p.wait(timeout=STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        p.kill()
    r = poll(token)
    r["stopped"] = reason
    return r


def zip_dir(d):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(d):
            for f in files:
                path = os.path.join(root, f)
                zf.write(path, os.path.relpath(path, d))
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        if self.path == "/healthz":
            return self._send(200, {"status": "healthy", "k6": K6_VERSION})
        if len(parts) >= 2 and parts[0] == "runs" and parts[1] in runs:
            token = parts[1]
            if len(parts) == 2:
                return self._send(200, poll(token))
            if parts[2] == "download":
                if poll(token)["status"] == "running":
                    return self._send(409, {"error": "run still running"})
                return self._send(200, zip_dir(run_dir(token)), "application/zip",
                                  [("Content-Disposition", f'attachment; filename="run_{token}.zip"')])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        try:
            if parts == ["runs", "start"]:
                body = self._json()
                if "script_base64" not in body:
                    return self._send(400, {"error": "script_base64 is required"})
                token = start_run(body)
                return self._send(201, {"token": token}, headers=[("Location", f"/runs/{token}")])
            if len(parts) == 3 and parts[0] == "runs" and parts[1] in runs and parts[2] == "stop":
                return self._send(200, stop_run(parts[1]))
        except (ValueError, KeyError, OSError) as exc:
            return self._send(400, {"error": str(exc)})
        self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):  # one line per request on stderr, no client noise
        print(f"{self.address_string()} {fmt % args}", flush=True)


def main():
    os.makedirs(DATA_PATH, exist_ok=True)
    if shutil.which(K6) is None:
        raise SystemExit(f"k6 binary {K6!r} not found")
    global K6_VERSION
    K6_VERSION = subprocess.run([K6, "version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    print(f"telek6 listening on :{HTTP_PORT}, data in {DATA_PATH}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
