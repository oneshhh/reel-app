FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Copy cookies if present (optional - baked in for Instagram auth)
# COPY cookies.txt /app/cookies.txt

RUN mkdir -p uploads output

EXPOSE 5000

# ⚠️  1 worker only — SSE jobs live in memory, multiple workers = 404 on /stream
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", "--timeout", "600", "app:app"]