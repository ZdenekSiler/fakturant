FROM python:3.13-slim

# weasyprint system deps (for HTML→PDF rendering)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-xlib-2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps before copying source so layer is cached on code changes
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" aiosqlite jinja2 pydantic httpx weasyprint \
    itsdangerous "python-multipart>=0.0.9" segno argon2-cffi

COPY . .

RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
