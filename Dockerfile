# One container = one OpenBrowser owner (tenant): own data volume, own keys, own public URL.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev && uv run --no-dev playwright install --with-deps chromium

ENV OPENBROWSER_DATA_DIR=/data
VOLUME /data
EXPOSE 8931
CMD ["uv", "run", "--no-dev", "openbrowser", "serve", "--host", "0.0.0.0", "--port", "8931"]
