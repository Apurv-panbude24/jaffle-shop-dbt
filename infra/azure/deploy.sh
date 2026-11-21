#!/usr/bin/env bash
# Deploys the Azure leg. Prereqs: `az login`, then:
#   export SQL_ADMIN_PASSWORD='...'   (12+ chars, upper/lower/digit/symbol; never commit it)
#   export ALERT_EMAIL='you@example.com'
#   ./infra/azure/deploy.sh [resource-group] [location]
set -euo pipefail

RG="${1:-rg-jaffle-shop}"
LOCATION="${2:-germanywestcentral}"
: "${SQL_ADMIN_PASSWORD:?Set SQL_ADMIN_PASSWORD}"
: "${ALERT_EMAIL:?Set ALERT_EMAIL}"

MY_IP="$(curl -s https://api.ipify.org || true)"

az group create --name "$RG" --location "$LOCATION" --output none
az deployment group create \
  --resource-group "$RG" \
  --template-file "$(dirname "$0")/main.bicep" \
  --parameters sqlAdminPassword="$SQL_ADMIN_PASSWORD" alertEmail="$ALERT_EMAIL" clientIp="$MY_IP" \
  --query properties.outputs --output json
