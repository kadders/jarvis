#!/usr/bin/env bash
# Purpose-named Kubernetes stack deploy for Jarvis.
# Stacks are machine-agnostic; registry / site overlays come from config + profiles.
#
# Examples:
#   ./deploy.sh tap-testing
#   ./deploy.sh --profile gpu-rig tap-testing
#   ./deploy.sh tap-collector
#   ./deploy.sh --list

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=deploy/lib.sh
source "${ROOT}/deploy/lib.sh"

deploy_color_init

DO_BUILD=1
DO_APPLY=1
LIST_ONLY=0
PROFILE="${DEPLOY_PROFILE:-}"
TARGET=""

usage() {
  cat <<EOF
Usage: $0 [options] <stack|service>
       $0 --list

Build, push, apply, and rollout a purpose-named stack (or a single service).

Options:
  --profile NAME     Load deploy/profiles/NAME.env (e.g. gpu-rig, microk8s)
  --apply-only       Skip docker build/push
  --build-only       Skip kubectl apply / rollout
  --list             List stacks and services
  -h, --help         Show this help

Config (first match wins for defaults):
  deploy.env                  Local overrides (gitignored)
  deploy/config.example.env   Committed defaults (REGISTRY=localhost:32000)

Environment: REGISTRY, IMAGE_TAG, NAMESPACE, KUBECTL, DEPLOY_PROFILE,
  STACK_OVERLAY_<name> (underscores, e.g. STACK_OVERLAY_tap_testing)

Compose local stacks: use docker compose / ./jarvis.sh — this script targets Kubernetes.

Examples:
  $0 tap-testing
  $0 --profile gpu-rig tap-testing
  $0 tap-collector
  $0 --apply-only --profile microk8s core
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:-}"
      [[ -n "$PROFILE" ]] || { deploy_err "--profile requires a name"; exit 1; }
      shift 2
      ;;
    --profile=*)
      PROFILE="${1#*=}"
      shift
      ;;
    --apply-only)
      DO_BUILD=0
      shift
      ;;
    --build-only)
      DO_APPLY=0
      shift
      ;;
    --list)
      LIST_ONLY=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      deploy_err "Unknown option: $1"
      usage
      exit 1
      ;;
    *)
      if [[ -n "$TARGET" ]]; then
        deploy_err "Unexpected extra argument: $1"
        exit 1
      fi
      TARGET="$1"
      shift
      ;;
  esac
done

deploy_load_config "$ROOT"

if [[ "$LIST_ONLY" -eq 1 ]]; then
  deploy_print_list
  exit 0
fi

if [[ -z "$TARGET" ]]; then
  usage
  exit 1
fi

# Profile from flag overrides config default; config may set DEPLOY_PROFILE.
if [[ -z "$PROFILE" && -n "${DEPLOY_PROFILE:-}" ]]; then
  PROFILE="$DEPLOY_PROFILE"
fi
if [[ -n "$PROFILE" ]]; then
  deploy_load_profile "$ROOT" "$PROFILE" || exit 1
  deploy_info "Profile: ${PROFILE}"
fi

CATALOG_PATH="$(deploy_find_catalog "$TARGET" || true)"
if [[ -z "$CATALOG_PATH" ]]; then
  deploy_err "Unknown stack or service: ${TARGET}"
  deploy_err "Run: $0 --list"
  exit 1
fi

deploy_load_catalog "$CATALOG_PATH"
deploy_info "Target: ${STACK_NAME} (${STACK_KIND}) — ${STACK_DESCRIPTION:-}"

if [[ "${STACK_COMPLETE}" != "1" ]]; then
  deploy_warn "Catalog entry marked incomplete (stub); proceeding anyway"
fi

if ! command -v docker >/dev/null 2>&1; then
  deploy_err "docker is required"
  exit 1
fi
if [[ "$DO_APPLY" -eq 1 ]] && ! command -v "${KUBECTL}" >/dev/null 2>&1; then
  deploy_err "${KUBECTL} is required for apply/rollout"
  exit 1
fi

OVERLAY="$(deploy_resolve_overlay "$ROOT")"

if [[ "$DO_BUILD" -eq 1 ]]; then
  deploy_build_push_images "$ROOT"
else
  deploy_warn "Skipping build/push (--apply-only)"
fi

if [[ "$DO_APPLY" -eq 1 ]]; then
  REWRITE_IMAGES=("${STACK_IMAGES[@]+"${STACK_IMAGES[@]}"}")
  if [[ "${STACK_KIND}" == "service" && -n "${STACK_PARENT:-}" ]]; then
    mapfile -t PARENT_IMAGES < <(deploy_parent_images "${STACK_PARENT}" || true)
    if [[ ${#PARENT_IMAGES[@]} -gt 0 ]]; then
      REWRITE_IMAGES=("${PARENT_IMAGES[@]}")
    fi
  fi
  if [[ ${#REWRITE_IMAGES[@]} -gt 0 ]]; then
    deploy_apply_overlay "$ROOT" "$OVERLAY" "${REWRITE_IMAGES[@]}"
  else
    deploy_apply_overlay "$ROOT" "$OVERLAY"
  fi
  deploy_rollout "$OVERLAY"
else
  deploy_warn "Skipping apply/rollout (--build-only)"
fi

deploy_info "Done: ${STACK_NAME}"
