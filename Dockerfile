# --- сборка фронтенда ---
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web ./
RUN npm run build            # → /frontend/dist (см. web/vite.config.ts)

# --- приложение ---
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md VERSION.txt ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY prompts ./prompts
COPY --from=web /frontend/dist ./frontend/dist
RUN uv sync --frozen --no-dev --no-install-project

ENV SYMPOSIUM_DATA_DIR=/data \
    SYMPOSIUM_HOST=0.0.0.0 \
    SYMPOSIUM_PORT=23606 \
    SYMPOSIUM_UI_PORT=23607
VOLUME /data
EXPOSE 23606 23607
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:23606/api/health')" || exit 1
CMD ["uv", "run", "python", "-m", "app.cli", "serve"]
