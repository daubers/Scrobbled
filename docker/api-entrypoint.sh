#!/bin/sh
# Apply database migrations (unless RUN_MIGRATIONS=0), then start the given command.
set -e
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  # Not under gunicorn, so don't use (or pollute) the multiprocess metrics directory.
  env -u PROMETHEUS_MULTIPROC_DIR flask --app scrobbler db upgrade
fi
exec "$@"
