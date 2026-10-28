"""Self-check: a real k6 through a real server. Needs k6 on PATH."""
import base64, io, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request, zipfile

SCRIPT = b"""
import { sleep } from 'k6';
export const options = { vus: 1, iterations: 2 };
export default function () { console.log(JSON.stringify({seq: __ITER, plan: open(__ENV.PLAN_PATH).trim()})); sleep(0.2); }
export function handleSummary(d) { return { stdout: JSON.stringify({kind: 'summary', n: d.metrics.iterations.values.count}) + '\\n' }; }
"""
# open() at module scope is a k6 rule; keep the plan read in init context.
SCRIPT = SCRIPT.replace(b"export default function () { console.log(JSON.stringify({seq: __ITER, plan: open(__ENV.PLAN_PATH).trim()}));",
                        b"const plan = open(__ENV.PLAN_PATH).trim();\nexport default function () { console.log(JSON.stringify({seq: __ITER, plan}));")


def call(url, body=None, method=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 method=method or ("POST" if body is not None else "GET"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read()


def main():
    port = 5199
    data = tempfile.mkdtemp()
    srv = subprocess.Popen([sys.executable, "app.py"], env={**os.environ, "TELEK6_HTTP_PORT": str(port), "TELEK6_DATA": data})
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                call(f"{base}/healthz"); break
            except OSError:
                time.sleep(0.1)
        health = json.loads(call(f"{base}/healthz")[1])
        local = subprocess.run(["k6", "version"], capture_output=True, text=True).stdout.splitlines()[0]
        assert health == {"status": "healthy", "k6": local}, (health, local)
        print(f"ok  healthz reports the k6 it runs: {local}")
        status, body = call(f"{base}/runs/start", {
            "script_base64": base64.b64encode(SCRIPT).decode(),
            "files": {"plan.ndjson": base64.b64encode(b"hello-plan\n").decode()},
            "env": {"PLAN_PATH": "plan.ndjson"}, "timeout_s": 30})
        assert status == 201, (status, body)
        token = json.loads(body)["token"]
        for _ in range(100):
            st = json.loads(call(f"{base}/runs/{token}")[1])
            if st["status"] == "finished": break
            time.sleep(0.2)
        assert st["status"] == "finished" and st["exit_code"] == 0, st
        print("ok  run finishes, exit_code 0")
        zf = zipfile.ZipFile(io.BytesIO(call(f"{base}/runs/{token}/download")[1]))
        names = set(zf.namelist())
        assert {"script.js", "plan.ndjson", "console.ndjson", "stdout.ndjson", "stderr.log"} <= names, names
        recs = [json.loads(l) for l in zf.read("console.ndjson").decode().splitlines() if l.startswith("{")]
        summ = [json.loads(l) for l in zf.read("stdout.ndjson").decode().splitlines() if l.startswith("{")]
        assert len(recs) == 2 and recs[0]["plan"] == "hello-plan", recs
        assert summ and summ[0]["n"] == 2, summ
        print("ok  download: script, files, console records and stdout summary come back in one zip")
        try:
            call(f"{base}/runs/nope")
            raise AssertionError("404 expected")
        except urllib.error.HTTPError as e:
            assert e.code == 404
        print("ok  unknown token is 404")
        # stop: a long script stopped mid-way still yields the summary
        long_script = SCRIPT.replace(b"iterations: 2", b"iterations: 1000")
        token = json.loads(call(f"{base}/runs/start", {"script_base64": base64.b64encode(long_script).decode(),
                                                       "files": {"plan.ndjson": "aGk="}, "env": {"PLAN_PATH": "plan.ndjson"}})[1])["token"]
        time.sleep(1.0)
        st = json.loads(call(f"{base}/runs/{token}/stop", {})[1])
        assert st["status"] == "finished" and st["stopped"] == "stop", st
        out = zipfile.ZipFile(io.BytesIO(call(f"{base}/runs/{token}/download")[1])).read("stdout.ndjson").decode()
        assert '"kind": "summary"' in out.replace('"kind":"summary"', '"kind": "summary"'), out[-300:]
        print("ok  stop: SIGINT ends the run and k6 still writes its summary")
        print("PASS")
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
