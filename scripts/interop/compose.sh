#!/usr/bin/env bash
# docker compose for the federation interop stack (Scrobbler + GoToSocial).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
# INTEROP_PROJECT (with a distinct SCROBBLER_IMAGE_TAG) lets concurrent runs (e.g. in CI) stay apart.
exec docker compose -p "${INTEROP_PROJECT:-scrobbler-interop}" \
  -f "$root/docker-compose.yml" -f "$root/deploy/interop/docker-compose.interop.yml" "$@"
