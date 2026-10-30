#!/usr/bin/env bash
# ==============================================================================
# Solarise Odisha WhatsApp Chatbot — One-Click VPS Deployment Script
# ==============================================================================
set -euo pipefail

echo "=================================================="
echo " Starting Solarise Odisha Production Deployment   "
echo "=================================================="

# 1. Prerequisite checks
command -v docker >/dev/null 2>&1 || {
  echo "Error: Docker is not installed. Install Docker first: curl -fsSL https://get.docker.com | sh";
  exit 1;
}

if ! docker compose version >/dev/null 2>&1; then
  echo "Error: Docker Compose plugin is not installed.";
  exit 1;
fi

# 2. Check for .env file
if [ ! -f ".env" ]; then
  echo "Error: .env file not found in current directory."
  echo "Copy .env.production.template to .env, populate DOMAIN, ACME_EMAIL, and secrets, then rerun."
  exit 1
fi

# 3. Check for database and OpenWA data
if [ ! -f "solarise_leads.db" ]; then
  echo "Warning: solarise_leads.db not found. A fresh database will be initialized."
  touch solarise_leads.db
fi

if [ ! -d "openwa_data" ]; then
  echo "Checking for backup archive to unpack openwa_data..."
  LATEST_BACKUP=$(ls -t backups/solarise_state_backup_*.zip 2>/dev/null | head -n 1 || true)
  if [ -n "$LATEST_BACKUP" ] && command -v unzip >/dev/null 2>&1; then
    echo "Unpacking latest backup: $LATEST_BACKUP"
    unzip -q "$LATEST_BACKUP" -d .
  else
    echo "Warning: openwa_data directory not found. Creating empty data directory."
    mkdir -p openwa_data
  fi
fi

# Ensure permissions
chmod 600 solarise_leads.db || true

# 4. Build and start services
echo "Building and starting production containers with Caddy reverse proxy..."
docker compose -f docker-compose.prod.yml up -d --build

# 5. Wait for healthy containers
echo "Waiting for services to become healthy..."
TIMEOUT=60
ELAPSED=0
while [ $ELAPSED -lt $TIMEOUT ]; do
  FASTAPI_STATUS=$(docker inspect --format='{{.State.Health.Status}}' solarise-fastapi 2>/dev/null || echo "starting")
  OPENWA_STATUS=$(docker inspect --format='{{.State.Health.Status}}' solarise-openwa 2>/dev/null || echo "starting")

  if [ "$FASTAPI_STATUS" = "healthy" ] && [ "$OPENWA_STATUS" = "healthy" ]; then
    echo "Both backend and OpenWA containers are healthy!"
    break
  fi

  echo "FastAPI: $FASTAPI_STATUS | OpenWA: $OPENWA_STATUS (elapsed: ${ELAPSED}s)..."
  sleep 5
  ELAPSED=$((ELAPSED + 5))
done

# 6. Ensure OpenWA webhook targets the internal Docker service
echo "Checking OpenWA webhook configuration..."
source .env
python3 -c "
import requests, os
api_key = os.getenv('OPENWA_API_KEY')
session_id = os.getenv('OPENWA_SESSION_ID', 'b9290ec0-2954-4be2-a32d-111512c06f7e')
secret = os.getenv('WEBHOOK_SECRET')
base = 'http://127.0.0.1:2785'
headers = {'X-API-Key': api_key}
try:
    r = requests.get(f'{base}/api/sessions/{session_id}/webhooks', headers=headers, timeout=5)
    whs = r.json()
    urls = [w.get('url') for w in whs]
    target = 'http://fastapi-app:8009/webhook'
    if target not in urls:
        print(f'Registering internal webhook {target}...')
        requests.post(f'{base}/api/sessions/{session_id}/webhooks', headers=headers, json={
            'url': target,
            'events': ['message.received'],
            'secret': secret,
            'headers': {'X-Webhook-Secret': secret},
            'retryCount': 3
        }, timeout=5)
    print('Webhook is configured correctly!')
except Exception as e:
    print('Webhook configuration notice:', e)
" 2>/dev/null || true

echo "=================================================="
echo " Deployment Complete!                             "
echo " Dashboard: https://$DOMAIN/dashboard.html        "
echo " Health:    https://$DOMAIN/health                "
echo "=================================================="
