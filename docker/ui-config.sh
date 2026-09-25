#!/bin/sh
# Point the UI at the API: rewrite config.js from $API_BASE_URL at container start.
set -e
case "$API_BASE_URL" in
  http://*|https://*) ;;
  *) echo "API_BASE_URL must start with http:// or https://" >&2; exit 1 ;;
esac
url=$(printf '%s' "$API_BASE_URL" | sed 's#/*$##; s#["\\]##g')
cat > /usr/share/nginx/html/config.js <<JS
window.SCROBBLER_CONFIG = {
  apiBaseUrl: "$url",
};
JS
