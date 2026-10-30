FROM python:3.11-slim


RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app


COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY yt-dlp_serv.py .

EXPOSE 8080


CMD ["python", "yt_dlp_serv.py"]