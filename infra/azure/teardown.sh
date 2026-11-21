#!/usr/bin/env bash
# Deletes EVERYTHING the deployment created (one resource group). Stops all cost.
#   ./infra/azure/teardown.sh [resource-group]
set -euo pipefail

RG="${1:-rg-jaffle-shop}"
read -r -p "Delete resource group '$RG' and everything in it? Type the name to confirm: " ANSWER
[ "$ANSWER" = "$RG" ] || { echo "Aborted."; exit 1; }
az group delete --name "$RG" --yes --no-wait
echo "Deletion started for $RG."
