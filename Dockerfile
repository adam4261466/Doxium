FROM python:3.11-slim
WORKDIR /app
<<<<<<< HEAD
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip cache purge
=======

RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip cache purge

>>>>>>> 7aad387 (i#)
COPY . .
RUN mkdir -p /data/faiss /data/uploads
