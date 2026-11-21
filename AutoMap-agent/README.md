# AutoMap Agent (Standalone)

Autonomous 24/7 YouTube Shorts agent separated from `AutoMap-cli`.

## Local Docker

```bash
# From the AutoMap-cli root
docker compose up -d --build
cd AutoMap-agent
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

The agent uses the private `automap-agent-link` network to reach AutoMap. The browser opens AutoMap at `http://localhost:3000`.

Open **http://localhost:4000**.

## Server / VPS Deployment (connects to remote AutoMap server)

1. Copy `example.env` to `.env`.
2. Set:
   - `AGENT_SERVER=TRUE`
   - `AGENT_WEB_USERNAME` & `AGENT_WEB_PASSWORD`
   - Optional `AGENT_SECRET_KEY` to override the secret persisted in `instance/`
   - `AUTOMAP_URL=https://your-automap-domain.com`
   - `AUTOMAP_PUBLIC_URL=https://your-automap-domain.com`
   - `WEB_LOGIN` & `WEB_PASSWORD` (if your AutoMap server has login enabled)
   - `AGENT_YOUTUBE_REDIRECT_URI=https://your-agent-domain.com/api/agent/youtube/callback`
3. Run `docker compose up -d --build`.
