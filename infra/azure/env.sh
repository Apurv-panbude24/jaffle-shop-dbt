#!/usr/bin/env bash
# Prints the environment variables the ingest UI needs, from the deployed resources.
# Use:  eval "$(./infra/azure/env.sh [resource-group])"
# The SQL password is not printed; the app reads ~/.config/jaffle-shop/sql_password
# (or AZURE_SQL_PASSWORD) itself.
set -euo pipefail

RG="${1:-rg-jaffle-shop}"
OUT="$(az deployment group show -g "$RG" -n main --query properties.outputs -o json)"
get() { python3 -c "import json,sys; print(json.loads(sys.argv[1])['$1']['value'])" "$OUT"; }

echo "export AZURE_SUBSCRIPTION_ID='$(az account show --query id -o tsv)'"
echo "export AZURE_RESOURCE_GROUP='$RG'"
echo "export AZURE_STORAGE_ACCOUNT='$(get storageAccountName)'"
echo "export AZURE_DATA_FACTORY='$(get dataFactoryName)'"
echo "export AZURE_SQL_SERVER='$(get sqlServerFqdn)'"
