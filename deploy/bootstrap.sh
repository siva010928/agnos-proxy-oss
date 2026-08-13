#!/usr/bin/env bash
# Agnos Proxy - GCE Bootstrap Script
# Usage: ./bootstrap.sh <DOMAIN> <GCP_PROJECT> [GCP_ZONE]
set -euo pipefail

DOMAIN="${1:?Usage: ./bootstrap.sh <DOMAIN> <GCP_PROJECT> [GCP_ZONE]}"
GCP_PROJECT="${2:?}"
GCP_ZONE="${3:-us-central1-a}"
VM_NAME="agnos-proxy-gateway"
MACHINE_TYPE="e2-standard-4"
REPO_URL="https://github.com/YOUR_GITHUB_USER/agnos-proxy.git"

echo "=== Creating GCE VM: $VM_NAME ==="
gcloud compute instances create "$VM_NAME" \
    --project="$GCP_PROJECT" \
    --zone="$GCP_ZONE" \
    --machine-type="$MACHINE_TYPE" \
    --image-family=ubuntu-2404-lts-amd64 \
    --image-project=ubuntu-os-cloud \
    --boot-disk-size=50GB \
    --tags=http-server,https-server \
    --metadata=startup-script='#!/bin/bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker ubuntu
systemctl enable docker
'

echo "=== Waiting for VM ==="
sleep 30

echo "=== Firewall rules ==="
gcloud compute firewall-rules create agnos-proxy-allow-http \
    --project="$GCP_PROJECT" \
    --allow=tcp:80,tcp:443 \
    --target-tags=http-server,https-server 2>/dev/null || true

echo "=== Deploying ==="
gcloud compute ssh "$VM_NAME" --project="$GCP_PROJECT" --zone="$GCP_ZONE" --command="
    while ! docker info >/dev/null 2>&1; do sleep 2; done
    cd /opt
    sudo git clone $REPO_URL agnos-proxy-gateway 2>/dev/null || (cd agnos-proxy-gateway && sudo git pull)
    cd agnos-proxy-gateway

    # .env must be filled with real keys by operator
    [ ! -f .env ] && sudo cp .env.example .env && echo 'FILL .env WITH REAL KEYS'

    export DOMAIN=$DOMAIN
    sudo docker compose -f deploy/docker-compose.prod.yml up -d --build

    echo 'Waiting for gateway...'
    for i in \$(seq 1 120); do
        curl -sf http://localhost:8090/health && break || sleep 2
    done

    # Seed + simulate
    sudo docker exec agnos-proxy-gateway python scripts/simulate_production.py 2>/dev/null || true
    echo '=== Deploy done: https://$DOMAIN ==='
"

EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
    --project="$GCP_PROJECT" --zone="$GCP_ZONE" \
    --format='get(networkInterfaces[0].accessConfigs[0].natIP)')

echo ""
echo "=== DONE ==="
echo "VM IP: $EXTERNAL_IP"
echo "URL:   https://$DOMAIN"
echo "Login: admin / agnos"
echo "DNS:   Point $DOMAIN A record -> $EXTERNAL_IP"
