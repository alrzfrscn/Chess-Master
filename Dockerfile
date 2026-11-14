# syntax=docker/dockerfile:1

# --- Frontend: vendor libraries + compiled Tailwind CSS -------------------
FROM node:20-alpine AS web
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY scripts ./scripts
COPY src/chess_master/web ./src/chess_master/web
RUN npm run build

# --- Runtime: Python API + static web UI ---------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHESS_MASTER_HOST=0.0.0.0 \
    PORT=8000 \
    STOCKFISH_PATH=/usr/games/stockfish

RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY --from=web /src/src/chess_master/web ./src/chess_master/web
RUN pip install --no-cache-dir . \
    && mkdir -p /app/state

VOLUME ["/app/state"]
EXPOSE 8000

CMD ["python", "-m", "chess_master", "--serve", "--host", "0.0.0.0", "--port", "8000"]
