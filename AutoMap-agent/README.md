# AutoMap Agent (Standalone)

Autonomous 24/7 YouTube Shorts agent separated from `AutoMap-cli`.

## Local Docker (connects to local AutoMap on `http://localhost:3000`)

```bash
docker compose up -d --build
```

Open **http://localhost:4000**.

## Server / VPS Deployment (connects to remote AutoMap server)

1. Copy `example.env` to `.env`.
2. Set:
   - `AGENT_SERVER=TRUE`
   - `AGENT_WEB_USERNAME` & `AGENT_WEB_PASSWORD`
   - `AGENT_SECRET_KEY`
   - `AUTOMAP_URL=https://your-automap-domain.com`
   - `AUTOMAP_PUBLIC_URL=https://your-automap-domain.com`
   - `WEB_LOGIN` & `WEB_PASSWORD` (if your AutoMap server has login enabled)
   - `AGENT_YOUTUBE_REDIRECT_URI=https://your-agent-domain.com/api/agent/youtube/callback`
   - `OAUTHLIB_INSECURE_TRANSPORT=0`
3. Run `docker compose up -d --build`.

## Code structure

- `web.py` assembles the Flask app and serves dashboard, settings, and approval actions.
- `agent/web_media.py` handles media routes; `agent/web_youtube.py` handles YouTube OAuth routes.
- `agent/web_services.py` resolves shared database and media paths from the current Flask app.
- `agent/runner.py` runs scheduling, queued commands, and Telegram interactions.
- `agent/video_generation.py` selects a topic and media, renders a candidate Short, and sends it for approval.
- `templates/agent.html` assembles dashboard sections from `templates/agent/`; `templates/login.html` contains the standalone sign-in form.
- `static/agent/` contains dashboard CSS and classic controllers for overview rendering, actions/settings, connections, and media. `bootstrap.js` loads last to initialize credentials and start polling after every controller is available.
