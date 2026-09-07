# telek6: python stdlib server + the k6 binary. Multi-stage, digest-pinned, non-root.
# k6 0.49.0 matches the one-shot Job Clue3 ran before telek6, so the transport
# swap changes nothing about what k6 does with the plan.
FROM grafana/k6:0.49.0@sha256:8cd78f9d0de5f50bc8821cceecf356d5d9e839e6611c226a3fcf13c591080fbd AS k6

FROM python:3.13-alpine@sha256:7415fbc3c9e4979cc717d92377ab2bc7b2b4a2af1ac03cc52b5f3f88efedaf3a
COPY --from=k6 /usr/bin/k6 /usr/bin/k6
RUN adduser -D -u 10001 telek6 && mkdir -p /data && chown telek6 /data
WORKDIR /app
COPY app.py .
USER 10001
ENV TELEK6_DATA=/data TELEK6_HTTP_PORT=5124
EXPOSE 5124
ENTRYPOINT ["python", "-u", "app.py"]
