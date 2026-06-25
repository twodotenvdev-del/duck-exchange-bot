FROM python:3.11-slim

  WORKDIR /app

  COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt

  COPY . .

  # Ensure the data directory exists (used when DB_PATH=/data/duck_exchange.db)
  RUN mkdir -p /data

  CMD ["python", "bot.py"]
  