#!/usr/bin/env bash
# Run the GoToSocial federation interop test end to end, then tear everything down.
# Usage: scripts/interop/run.sh [--keep]   (--keep leaves the stack running afterwards)
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
compose="$here/compose.sh"
keep=0; [ "${1:-}" = "--keep" ] && keep=1

cleanup() { [ "$keep" = 1 ] || "$compose" down -v --remove-orphans >/dev/null 2>&1; }
trap cleanup EXIT

"$compose" down -v --remove-orphans >/dev/null 2>&1
echo "==> Building and starting Scrobbler + GoToSocial"
"$compose" up -d --build --wait db api ui worker tls gotosocial || { "$compose" ps; exit 1; }

echo "==> Waiting for GoToSocial"
for _ in $(seq 1 60); do
  "$compose" exec -T gotosocial wget -qO- http://127.0.0.1/api/v1/instance >/dev/null 2>&1 && break
  sleep 2
done

echo "==> Creating the GoToSocial test user"
"$compose" exec -T gotosocial /gotosocial/gotosocial admin account create \
  --username gtsuser --email gtsuser@gts.test --password 'Interop-Test-Password-1' >/dev/null
"$compose" exec -T gotosocial /gotosocial/gotosocial admin account confirm --username gtsuser >/dev/null

"$compose" build driver >/dev/null
"$compose" run --rm driver
status=$?
if [ $status -ne 0 ]; then
  echo "==> Scrobbler federation state"
  "$compose" exec -T db psql -U scrobbler -d scrobbler -c \
    "select activity_type, status, reason, attempts from federation_inbox order by id" -c \
    "select a.activity_type, d.status, d.attempts, d.last_status, left(d.last_error, 200) as error
       from federation_deliveries d join federation_activities a on a.id = d.activity_id order by d.id"
  echo "==> Recent logs"
  "$compose" logs --tail 200 worker gotosocial | grep -v "api/v1/accounts/relationships" | tail -40
fi
exit $status
