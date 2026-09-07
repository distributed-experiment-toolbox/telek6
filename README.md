# telek6

A long-lived k6 runner with TeleLocust's REST contract, for the Sustainability Measurement
Agent (SMA). The SMA client lives in SMA under `modules/telek6/`; this repo is the server.

| Method | Path | Body / result |
|---|---|---|
| POST | `/runs/start` | `{script_base64, files?: {name: b64}, env?: {K: V}, args?: [k6 args], timeout_s?}` → `201 {token}` |
| GET | `/runs/<token>` | `{status: running\|finished, exit_code, started_at, finished_at}` |
| POST | `/runs/<token>/stop` | SIGINT k6 (handleSummary still runs), SIGKILL after `TELEK6_STOP_GRACE_S` |
| GET | `/runs/<token>/download` | zip of the run dir: `script.js`, the files, `console.ndjson` (console.log lines), `stdout.ndjson` (handleSummary's stdout), `stderr.log` (k6's log), anything else the script wrote |
| GET | `/healthz` | |

k6 runs as `k6 run --log-format=raw --console-output=console.ndjson <args> script.js` in the
run directory with `env` added, so a script's `console.log` lines arrive in `console.ndjson`
unprefixed and a `handleSummary` that returns `{stdout: ...}` lands in `stdout.ndjson`.

It executes whatever script it is sent: cluster-internal only, no auth, same trust model as
TeleLocust.

```sh
python3 app.py                       # needs k6 on PATH; TELEK6_HTTP_PORT=5124, TELEK6_DATA=./data
python3 test_app.py                  # start a server, run a 1 s script through it, check the zip
docker build -t telek6 .
kubectl apply -f k8s/telek6.yaml     # control node, NodePort 30124
```
