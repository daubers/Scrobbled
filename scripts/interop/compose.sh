#!/usr/bin/env bash
# docker compose for the federation interop stack (Scrobbler + GoToSocial).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
# INTEROP_PROJECT and INTEROP_SUBNET let concurrent runs (e.g. in CI) stay apart.
exec docker compose -p "${INTEROP_PROJECT:-scrobbler-interop}" \
  -f "$root/docker-compose.yml" -f "$root/deploy/interop/docker-compose.interop.yml" "$@"
