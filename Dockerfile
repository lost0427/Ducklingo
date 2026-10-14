FROM oven/bun:1.3 AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
RUN bun run build

FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg python3 python3-pip python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN python3 -m venv /opt/venv --system-site-packages \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install \
      --extra-index-url https://crispstrobe.github.io/CrispASR/whl/cuda/ \
      fastapi uvicorn argon2-cffi httpx crispasr==0.8.31+cuda numpy tiktoken

COPY backend/ ./backend/
COPY serve.py ./serve.py
COPY config/config.example.toml ./config/config.example.toml
COPY config/asr_models.example.toml ./config/asr_models.example.toml
COPY --from=frontend /app/frontend/dist ./frontend/dist

RUN mkdir -p /app/data && chmod 777 /app/data
VOLUME ["/app/data"]
EXPOSE 8000
ENV PATH="/opt/venv/bin:${PATH}"
CMD ["python", "serve.py", "--host", "::", "--port", "8000"]
