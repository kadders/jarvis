#!/usr/bin/env bash
# Shared helpers for purpose-named stack deploy (sourced by deploy.sh).

# Repo root (parent of deploy/).
deploy_repo_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "${here}/.." && pwd
}

deploy_color_init() {
  if [[ -t 1 ]]; then
    DEPLOY_GREEN='\033[0;32m'
    DEPLOY_YELLOW='\033[1;33m'
    DEPLOY_RED='\033[0;31m'
    DEPLOY_BLUE='\033[0;34m'
    DEPLOY_NC='\033[0m'
  else
    DEPLOY_GREEN=''
    DEPLOY_YELLOW=''
    DEPLOY_RED=''
    DEPLOY_BLUE=''
    DEPLOY_NC=''
  fi
}

deploy_info() { echo -e "${DEPLOY_GREEN}$*${DEPLOY_NC}"; }
deploy_warn() { echo -e "${DEPLOY_YELLOW}$*${DEPLOY_NC}"; }
deploy_err() { echo -e "${DEPLOY_RED}$*${DEPLOY_NC}" >&2; }

# Convert stack/service name to env key suffix (tap-testing -> tap_testing).
deploy_name_key() {
  echo "${1//-/_}"
}

# Load global config from deploy.env when present; then apply portable defaults.
deploy_load_config() {
  local root="$1"
  if [[ -f "${root}/deploy.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "${root}/deploy.env"
    set +a
  fi

  : "${REGISTRY:=localhost:32000}"
  : "${IMAGE_TAG:=latest}"
  : "${KUBECTL:=kubectl}"
  NAMESPACE="${NAMESPACE:-}"
  DEPLOY_PROFILE="${DEPLOY_PROFILE:-}"
}

# Load optional profile (gpu-rig, microk8s, …). Sets overlay overrides + kube env.
deploy_load_profile() {
  local root="$1"
  local profile="$2"
  [[ -n "$profile" ]] || return 0

  local path="${root}/deploy/profiles/${profile}.env"
  if [[ ! -f "$path" ]]; then
    deploy_err "Unknown profile: ${profile} (expected ${path})"
    return 1
  fi
  # shellcheck source=/dev/null
  source "$path"

  if [[ -n "${DEPLOY_KUBECONFIG_ENV:-}" && -f "${DEPLOY_KUBECONFIG_ENV}" ]]; then
    # shellcheck source=/dev/null
    source "${DEPLOY_KUBECONFIG_ENV}"
    deploy_info "Loaded kube env from ${DEPLOY_KUBECONFIG_ENV}"
  fi
}

deploy_stacks_dir() {
  echo "$(deploy_repo_root)/deploy/stacks"
}

# List catalog basenames (without .env), one per line.
deploy_list_catalog_names() {
  local d
  d="$(deploy_stacks_dir)"
  [[ -d "$d" ]] || return 0
  local f
  for f in "$d"/*.env; do
    [[ -e "$f" ]] || continue
    basename "$f" .env
  done | sort
}

# Resolve catalog path for name or alias. Prints path or empty.
deploy_find_catalog() {
  local want="$1"
  local d f name line aliases
  d="$(deploy_stacks_dir)"
  for f in "$d"/*.env; do
    [[ -e "$f" ]] || continue
    name="$(basename "$f" .env)"
    if [[ "$name" == "$want" ]]; then
      echo "$f"
      return 0
    fi
    line="$(grep -E '^STACK_ALIASES=' "$f" 2>/dev/null | head -n1 || true)"
    if [[ -n "$line" ]]; then
      aliases="${line#STACK_ALIASES=}"
      aliases="${aliases%\"}"
      aliases="${aliases#\"}"
      aliases="${aliases%\'}"
      aliases="${aliases#\'}"
      for a in $aliases; do
        if [[ "$a" == "$want" ]]; then
          echo "$f"
          return 0
        fi
      done
    fi
  done
  return 1
}

# Source a catalog file and export STACK_* into the current shell.
deploy_load_catalog() {
  local path="$1"
  unset STACK_NAME STACK_ALIASES STACK_DESCRIPTION STACK_KIND STACK_PARENT
  unset STACK_OVERLAY_DEFAULT STACK_IMAGES STACK_DEPLOYMENTS STACK_COMPLETE
  STACK_KIND=stack
  STACK_COMPLETE=1
  # shellcheck source=/dev/null
  source "$path"
  STACK_NAME="${STACK_NAME:-$(basename "$path" .env)}"
  STACK_KIND="${STACK_KIND:-stack}"
  STACK_COMPLETE="${STACK_COMPLETE:-1}"
}

# Resolve overlay path for the currently loaded STACK_* (+ optional parent).
deploy_resolve_overlay() {
  local root="$1"
  local key parent_key override override_var

  key="$(deploy_name_key "${STACK_NAME}")"
  override_var="STACK_OVERLAY_${key}"
  override="${!override_var:-}"
  if [[ -n "$override" ]]; then
    echo "${root}/${override}"
    return 0
  fi

  if [[ "${STACK_KIND}" == "service" && -n "${STACK_PARENT:-}" ]]; then
    parent_key="$(deploy_name_key "${STACK_PARENT}")"
    override_var="STACK_OVERLAY_${parent_key}"
    override="${!override_var:-}"
    if [[ -n "$override" ]]; then
      echo "${root}/${override}"
      return 0
    fi
  fi

  if [[ -z "${STACK_OVERLAY_DEFAULT:-}" ]]; then
    deploy_err "STACK_OVERLAY_DEFAULT unset for ${STACK_NAME}"
    return 1
  fi
  echo "${root}/${STACK_OVERLAY_DEFAULT}"
}

# Build and push one image spec: name|dockerfile|context
deploy_build_push_image() {
  local root="$1"
  local spec="$2"
  local name dockerfile context
  IFS='|' read -r name dockerfile context <<<"$spec"
  local tag="${REGISTRY}/${name}:${IMAGE_TAG}"
  deploy_info "Building ${tag}"
  DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}" docker build \
    -t "$tag" \
    -f "${root}/${dockerfile}" \
    "${root}/${context}"
  deploy_info "Pushing ${tag}"
  docker push "$tag"
}

deploy_build_push_images() {
  local root="$1"
  local spec
  if [[ ${#STACK_IMAGES[@]} -eq 0 ]]; then
    deploy_warn "No images to build for ${STACK_NAME}"
    return 0
  fi
  for spec in "${STACK_IMAGES[@]}"; do
    deploy_build_push_image "$root" "$spec"
  done
}

# Apply overlay with REGISTRY/IMAGE_TAG image rewrites via a temp wrapper.
# Extra image specs (name|dockerfile|context) may be passed as remaining args for rewrite-only.
deploy_apply_overlay() {
  local root="$1"
  local overlay="$2"
  shift 2
  local rewrite_specs=("${STACK_IMAGES[@]+"${STACK_IMAGES[@]}"}")
  if [[ $# -gt 0 ]]; then
    rewrite_specs=("$@")
  fi
  local tmp images_yaml="" spec name

  if [[ ! -d "$overlay" ]]; then
    deploy_err "Overlay not found: ${overlay}"
    return 1
  fi

  tmp="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-deploy.XXXXXX")"
  # shellcheck disable=SC2064
  trap "rm -rf '${tmp}'" RETURN

  images_yaml=""
  for spec in "${rewrite_specs[@]+"${rewrite_specs[@]}"}"; do
    [[ -n "$spec" ]] || continue
    IFS='|' read -r name _ _ <<<"$spec"
    images_yaml+="  - name: ${name}"$'\n'
    images_yaml+="    newName: ${REGISTRY}/${name}"$'\n'
    images_yaml+="    newTag: ${IMAGE_TAG}"$'\n'
  done

  {
    echo "apiVersion: kustomize.config.k8s.io/v1beta1"
    echo "kind: Kustomization"
    echo "resources:"
    echo "  - ${overlay}"
    # Inject namespace only when the overlay does not already declare one.
    if [[ -n "${NAMESPACE:-}" ]] && ! grep -qE '^namespace:' "${overlay}/kustomization.yaml" 2>/dev/null; then
      echo "namespace: ${NAMESPACE}"
    fi
    if [[ -n "$images_yaml" ]]; then
      echo "images:"
      printf '%s' "$images_yaml"
    fi
  } >"${tmp}/kustomization.yaml"

  deploy_info "Applying ${overlay} (registry=${REGISTRY} tag=${IMAGE_TAG})"
  "${KUBECTL}" apply -k "${tmp}"
}

# For a service target, return parent stack image specs (for apply-time rewrite).
deploy_parent_images() {
  local parent="$1"
  local path
  path="$(deploy_find_catalog "$parent")" || return 1
  (
    unset STACK_IMAGES
    # shellcheck source=/dev/null
    source "$path"
    printf '%s\n' "${STACK_IMAGES[@]+"${STACK_IMAGES[@]}"}"
  )
}

deploy_rollout() {
  local overlay="${1:-}"
  local ns="${NAMESPACE:-}"
  local ns_args=()
  if [[ -z "$ns" && -n "$overlay" && -f "${overlay}/kustomization.yaml" ]]; then
    ns="$(grep -E '^namespace:' "${overlay}/kustomization.yaml" 2>/dev/null | head -n1 | awk '{print $2}' || true)"
  fi
  if [[ -n "$ns" ]]; then
    ns_args=(-n "$ns")
  fi
  local dep
  if [[ ${#STACK_DEPLOYMENTS[@]} -eq 0 ]]; then
    deploy_warn "No deployments to restart for ${STACK_NAME}"
    return 0
  fi
  for dep in "${STACK_DEPLOYMENTS[@]}"; do
    deploy_info "Rollout restart deploy/${dep}${ns:+ (ns=${ns})}"
    "${KUBECTL}" "${ns_args[@]}" rollout restart "deploy/${dep}"
    "${KUBECTL}" "${ns_args[@]}" rollout status "deploy/${dep}" --timeout=180s
  done
}

deploy_print_list() {
  local d f
  d="$(deploy_stacks_dir)"
  echo -e "${DEPLOY_BLUE}Stacks and services${DEPLOY_NC}"
  echo
  printf '%-18s %-10s %-8s %s\n' "NAME" "KIND" "READY" "DESCRIPTION"
  printf '%-18s %-10s %-8s %s\n' "----" "----" "-----" "-----------"
  for f in "$d"/*.env; do
    [[ -e "$f" ]] || continue
    deploy_load_catalog "$f"
    local ready="yes"
    [[ "${STACK_COMPLETE}" == "1" ]] || ready="stub"
    local aliases=""
    [[ -n "${STACK_ALIASES:-}" ]] && aliases=" (aliases: ${STACK_ALIASES})"
    printf '%-18s %-10s %-8s %s%s\n' \
      "${STACK_NAME}" "${STACK_KIND}" "${ready}" "${STACK_DESCRIPTION:-}${aliases}"
  done
}
