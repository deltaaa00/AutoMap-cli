# Build the shared runtime image used by both agent-web and agent-runner.
# Compose selects Gunicorn for web.py; the default command runs agent.runner.
# ffmpeg/ffprobe support media splitting. COPY agent/ includes route Blueprints
# and video generation modules. COPY templates/ and static/ includes dashboard
# partials/controllers; instance/media data are mounted by Compose.

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY web.py ./
COPY templates/ ./templates/
COPY static/ ./static/
COPY agent/ ./agent/

EXPOSE 4000
CMD ["python", "-m", "agent.runner"]
