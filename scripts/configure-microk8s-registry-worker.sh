#!/usr/bin/env bash
# Configure a MicroK8s worker node to pull from the gpu-rig HTTP registry.
# Run on fridge-prime (or any worker) with sudo.
set -euo pipefail

REGISTRY_HOST="${REGISTRY_HOST:-192.168.86.120:32000}"
REGISTRY_URL="http://${REGISTRY_HOST}"

if ! command -v snap >/dev/null 2>&1 || ! snap list microk8s >/dev/null 2>&1; then
  echo "microk8s snap not found; skipping containerd config." >&2
  exit 1
fi

CERTS_ROOT="$(readlink -f /var/snap/microk8s/current/args/certs.d)"
TARGET="${CERTS_ROOT}/${REGISTRY_HOST}"
mkdir -p "${TARGET}"
cat >"${TARGET}/hosts.toml" <<EOF
server = "${REGISTRY_URL}"

[host."${REGISTRY_URL}"]
  capabilities = ["pull", "resolve"]
EOF
chown root:microk8s "${TARGET}/hosts.toml"
chmod 640 "${TARGET}/hosts.toml"
echo "Wrote ${TARGET}/hosts.toml"

if command -v docker >/dev/null 2>&1; then
  DAEMON_JSON=/etc/docker/daemon.json
  if [[ -f "${DAEMON_JSON}" ]] && grep -q "${REGISTRY_HOST}" "${DAEMON_JSON}"; then
    echo "Docker already lists ${REGISTRY_HOST} in insecure-registries."
  else
    echo "Tip: add \"insecure-registries\": [\"${REGISTRY_HOST}\"] to ${DAEMON_JSON} for docker pull/push."
  fi
fi

echo "Restarting microk8s to reload containerd registry config..."
snap restart microk8s
echo "Done. Verify with:"
echo "  curl -s ${REGISTRY_URL}/v2/_catalog"
echo "  kubectl run regtest --rm -it --restart=Never --image=${REGISTRY_HOST}/jarvis_ui:latest --command -- echo ok"
