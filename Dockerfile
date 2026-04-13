FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip cache purge

COPY . .

RUN mkdir -p /data/faiss /data/uploads

CMD ["/bin/sh", "-c", "gunicorn 'app:create_app()' --bind 0.0.0.0:${PORT:-8000} --workers 2 --timeout 120"]
