# Dockerfile
FROM python:3.12-slim

# Устанавливаем необходимые системные пакеты (для сборки aiohttp и зависимостей)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libssl-dev \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

CMD ["python", "main.py"]
