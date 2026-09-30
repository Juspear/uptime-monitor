# Deploying to a VPS

This guide runs uptime-monitor 24/7 on any small Linux server (Ubuntu 22.04 / 24.04) with Docker.
The cheapest VPS tier is enough: the monitor uses ~30 MB of RAM and almost no CPU.

## 1. Connect to the server

```bash
ssh root@YOUR_SERVER_IP
```

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version && docker compose version
```

## 3. Get the code

```bash
git clone https://github.com/Juspear/uptime-monitor.git
cd uptime-monitor
```

## 4. Configure

```bash
cp config.example.toml config.toml
nano config.toml          # list the sites you want to watch

cp .env.example .env
nano .env                 # paste your bot token and chat id (no quotes)
chmod 600 .env            # only you can read the secrets
```

## 5. Start

```bash
docker compose up -d --build
```

The container restarts automatically after crashes and server reboots (`restart: unless-stopped`).

## Everyday commands

| Task | Command |
|---|---|
| See live logs | `docker compose logs -f` |
| Check it is running | `docker compose ps` |
| Apply config changes | `docker compose restart` |
| Update to the latest version | `git pull && docker compose up -d --build` |
| Stop | `docker compose down` |

Log times are in UTC.

## Quick test

To make sure alerts reach Telegram, temporarily add a site that cannot work:

```toml
[[sites]]
name = "Test Down"
url = "https://nonexistent.example.invalid"
interval = 10
```

Run `docker compose restart`, wait ~20 seconds for the 🔴 alert, then remove the block and restart again.
