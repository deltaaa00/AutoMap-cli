FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY web.py ./
COPY templates/ ./templates/
COPY agent/ ./agent/

EXPOSE 4000
CMD ["python", "-m", "agent.runner"]
