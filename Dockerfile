FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip cache purge

COPY . .

RUN mkdir -p /data/faiss /data/uploads

CMD gunicorn "app:create_app()" --bind "0.0.0.0:8080" --workers 2 --timeout 120
