#!/usr/bin/env bash
# Remove oximeter collector artifacts from jarvis-llm after migration to namespace health.
# Safe to run when oximeter is deployed via k8s/overlays/gpu-rig-health/ only.
set -euo pipefail

NS=jarvis-llm
PV_OLD=nfs-fridge-oximeter-data

echo "==> Checking jarvis-llm for oximeter resources..."
FOUND=0
for kind in deployment service configmap pvc; do
  if kubectl -n "$NS" get "$kind" 2>/dev/null | grep -qi oximeter; then
    FOUND=1
  fi
done

if [[ "$FOUND" -eq 1 ]]; then
  echo "Deleting oximeter resources from $NS..."
  kubectl -n "$NS" delete deploy/oximeter-collector svc/oximeter-collector \
    configmap/jarvis-oximeter-collector-config pvc/oximeter-collector-data \
    --ignore-not-found
else
  echo "No oximeter deploy/svc/cm/pvc in $NS."
fi

if kubectl get pv "$PV_OLD" &>/dev/null; then
  echo "Deleting stale PV $PV_OLD..."
  kubectl delete pv "$PV_OLD" --ignore-not-found
else
  echo "No stale PV $PV_OLD."
fi

OLD_NFS=/media/kad/data/k8s/jarvis-llm/oximeter-data
if [[ -d "$OLD_NFS" ]]; then
  echo "Removing old NFS host path $OLD_NFS (requires sudo)..."
  sudo rm -rf "$OLD_NFS"
else
  echo "No old NFS path $OLD_NFS."
fi

echo "==> Remaining oximeter objects (should be health namespace only):"
kubectl get deploy,svc,cm,pvc,pv -A 2>/dev/null | grep -i oximeter || echo "none"
echo "Done."
