FROM brainicism/bgutil-ytdlp-pot-provider:1.3.1-deno

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 \
       python3-venv \
       ffmpeg \
       ca-certificates \
       curl \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:/usr/bin:$PATH"

WORKDIR /clipforge

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=10000
ENV POT_BASE_URL=http://127.0.0.1:4416
ENV YOUTUBE_CLIENTS=mweb
ENV YTDLP_JS_RUNTIME=deno
ENV YTDLP_SLEEP_REQUESTS=0.75

ENTRYPOINT []

CMD ["sh", "-c", "/usr/bin/deno run --allow-env --allow-net --allow-ffi=/app/node_modules --allow-read=/app/node_modules /app/src/main.ts --port 4416 & provider_pid=$!; ready=0; for i in $(seq 1 30); do if curl -fsS http://127.0.0.1:4416/ping >/dev/null; then ready=1; break; fi; sleep 1; done; if [ \"$ready\" != \"1\" ]; then echo 'PO-token provider failed to start'; kill $provider_pid 2>/dev/null || true; exit 1; fi; exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
