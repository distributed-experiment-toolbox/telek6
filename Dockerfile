# telek6: python stdlib server + the k6 binary. Multi-stage, digest-pinned, non-root.
# k6 2.2.0, the version Clue3 pins (clue3/workload/k6.py K6_VERSION) and tests
# replay.js against; its preflight refuses any other. Was 0.49.0 until 2026-10-28,
# carried over from a throwaway Job, while every local probe ran 2.2.0.
# Index digest: amd64 (ISE) and arm64 (kind on Apple silicon).
FROM grafana/k6:2.2.0@sha256:9bd01d6941fca969cb61bb57d2da5ee9b385fe2aa8881df3798c196564d6ace6 AS k6

FROM python:3.13-alpine@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a
COPY --from=k6 /usr/bin/k6 /usr/bin/k6
RUN adduser -D -u 10001 telek6 && mkdir -p /data && chown telek6 /data
WORKDIR /app
COPY app.py .
USER 10001
ENV TELEK6_DATA=/data TELEK6_HTTP_PORT=5124
EXPOSE 5124
ENTRYPOINT ["python", "-u", "app.py"]
