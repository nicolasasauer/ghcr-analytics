# GHCR-Pulse 📦

A self-hosted analytics dashboard for tracking **GitHub Container Registry (GHCR)** pull statistics over time.

> **⚡ Vibe-Coded**
> This project was built with the help of AI-assisted development. The code works, but may not follow all conventional best practices. Use at your own risk – contributions and improvements are always welcome!

![Dashboard Screenshot](https://github.com/user-attachments/assets/0296ad00-5726-4ed2-bf54-5d0c9e0df57e)

---

## Features

- 📊 **Pull-count timeline charts** for every tracked container image
- 🔢 **KPI cards** – total repositories, total pulls, and 24-hour growth
- ➕ **Add / remove** packages with a single click
- 🔄 **Automatic background refresh** (configurable interval)
- 🔐 **Optional HTTP Basic Auth** to protect the dashboard
- 🌙 **Dark-mode UI** out of the box
- 🐳 **Single Docker image** – no external database required (SQLite on a named volume)

---

## Quick Start (recommended)

The pre-built image is published to the GitHub Container Registry and is **publicly accessible** – no login required.

```bash
# 1. Create a .env file (copy the example below)
# 2. Run
docker compose up -d
```

### `docker-compose.yml`

```yaml
services:
  ghcr-analytics:
    image: ghcr.io/nicolasasauer/ghcr-analytics:latest
    container_name: ghcr-analytics
    restart: unless-stopped
    ports:
      - "${PORT:-8000}:8000"
    volumes:
      - stats_db:/data
    environment:
      GITHUB_TOKEN: "${GITHUB_TOKEN:-}"
      UPDATE_INTERVAL_HOURS: "${UPDATE_INTERVAL_HOURS:-6}"
      AUTH_USER: "${AUTH_USER:-}"
      AUTH_PASSWORD: "${AUTH_PASSWORD:-}"
      DB_PATH: /data/stats.db
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 15s

volumes:
  stats_db:
    name: stats_db
```

Open your browser at **http://localhost:8000** once the container is running.

---

## Configuration

All settings are passed via environment variables (or a `.env` file next to `docker-compose.yml`).

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | _(empty)_ | Personal Access Token with `read:packages` scope. Required for private packages and to raise the API rate limit. |
| `UPDATE_INTERVAL_HOURS` | `6` | How often (in hours) pull stats are refreshed in the background. |
| `AUTH_USER` | _(empty)_ | HTTP Basic Auth username. Leave empty to disable auth. |
| `AUTH_PASSWORD` | _(empty)_ | HTTP Basic Auth password. Leave empty to disable auth. |
| `PORT` | `8000` | Host port to bind (only used by Docker Compose). |
| `DB_PATH` | `/data/stats.db` | Path to the SQLite database file inside the container. |

---

## Screenshots

### Dashboard

![Dashboard – KPI cards & charts](https://github.com/user-attachments/assets/0296ad00-5726-4ed2-bf54-5d0c9e0df57e)

The main dashboard shows:
- **KPI cards** at the top (tracked repositories, total pulls, 24-hour growth)
- **Pull-count timeline charts** for each tracked image (interactive: zoom & pan)
- **Add package** form and **remove** button per repository

### Manage Packages & Charts

![Charts grid & package management](https://github.com/user-attachments/assets/5ae59a07-d804-4145-8c5a-f38f915dc9c6)

### Adding a Package

Enter the package in `owner/package-name` format (e.g. `nicolasasauer/ghcr-analytics`) and click **Add**.

---

## CI/CD – Automatic Docker Builds

A GitHub Actions workflow (`.github/workflows/docker-publish.yml`) automatically builds and pushes the image to GHCR on every push to `main`:

```
ghcr.io/nicolasasauer/ghcr-analytics:latest
```

The package visibility is set to **public** in the GitHub repository settings
(*Packages → ghcr-analytics → Package settings → Change visibility → Public*),
so anyone can pull the image without authentication.

### Making the GHCR Package Public

After the first successful workflow run:

1. Go to **https://github.com/nicolasasauer?tab=packages**
2. Click on **ghcr-analytics**
3. Click **Package settings** (bottom-right)
4. Under *Danger Zone* → **Change visibility** → select **Public**

---

## Building from Source

```bash
git clone https://github.com/nicolasasauer/ghcr-analytics.git
cd ghcr-analytics
docker build -t ghcr-analytics .
docker run -p 8000:8000 ghcr-analytics
```

---

## Development

```bash
# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the app locally
uvicorn app.main:app --reload
```

The app will be available at **http://localhost:8000**.

---

## License

MIT

