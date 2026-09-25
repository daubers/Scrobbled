#!/usr/bin/env bash
# docker compose for the federation interop stack (Scrobbler + GoToSocial).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
exec docker compose -f "$root/docker-compose.yml" -f "$root/deploy/interop/docker-compose.interop.yml" "$@"
