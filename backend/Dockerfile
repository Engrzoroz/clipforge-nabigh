FROM brainicism/bgutil-ytdlp-pot-provider:1.3.1

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /clipforge

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=10000
ENV POT_BASE_URL=http://127.0.0.1:4416

ENTRYPOINT []
CMD ["sh", "-c", "node /app/build/main.js --port 4416 & sleep 1; exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
