FROM node:20.19.4-bookworm-slim AS frontend

WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY app ./app
COPY public ./public
COPY scripts ./scripts
COPY next.config.js tsconfig.json eslint.config.mjs ./
RUN npm run build


FROM python:3.12.11-slim-bookworm AS wheel

WORKDIR /src
RUN python -m pip install --no-cache-dir build
COPY pyproject.toml README.md ./
COPY biliup ./biliup
COPY --from=frontend /src/biliup/web/public ./biliup/web/public
RUN python -m build --wheel --outdir /wheels


FROM python:3.12.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BILIUP_HOME=/data \
    BILIUP_DATA_DIR=/data \
    BILIUP_DATABASE=/data/data.sqlite3 \
    BILIUP_FRONTEND_DIR=/app/out

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=wheel /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels
COPY --from=frontend /src/out /app/out

WORKDIR /data
EXPOSE 19159
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/tini", "--", "biliup"]
CMD ["server", "--host", "0.0.0.0", "--port", "19159"]
