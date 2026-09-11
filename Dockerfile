FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        rsync \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY src ./src

CMD ["uv", "run", "--no-dev", "src/backuper/main.py"]
