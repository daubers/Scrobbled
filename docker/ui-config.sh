#!/bin/sh
# Container start-up: point the UI at the API, and (when API_INTERNAL_URL is set)
# forward the fediverse paths on this domain to the API.
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

mkdir -p /etc/nginx/scrobbler
if [ -z "$API_INTERNAL_URL" ]; then
  : > /etc/nginx/scrobbler/federation.conf
  exit 0
fi
case "$API_INTERNAL_URL" in
  http://*|https://*) ;;
  *) echo "API_INTERNAL_URL must look like http://api:8000" >&2; exit 1 ;;
esac
upstream=$(printf '%s' "$API_INTERNAL_URL" | sed 's#/*$##')
case "$upstream" in
  *://*/*) echo "API_INTERNAL_URL must not have a path" >&2; exit 1 ;;
esac
# Resolve the API's name per request (not once at start-up), so the UI starts even if
# the API isn't up yet and keeps working when the API container is replaced.
nameserver=$(awk '/^nameserver/ { print $2; exit }' /etc/resolv.conf)
case "$nameserver" in *:*) nameserver="[$nameserver]" ;; esac
cat > /etc/nginx/scrobbler/federation.conf <<NGINX
# Fediverse (ActivityPub) paths live on the UI's domain; the API answers them.
location ~ ^/(\.well-known/(webfinger|nodeinfo|host-meta)\$|nodeinfo/|users/|inbox\$|actor(/|\$)) {
    resolver ${nameserver:-127.0.0.11} valid=30s ipv6=off;
    set \$scrobbler_api $upstream;
    proxy_pass \$scrobbler_api;
    proxy_set_header Host \$host;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scrobbler_forwarded_proto;
    proxy_set_header X-Forwarded-Host \$host;
    client_max_body_size 1m;
}
NGINX
echo "forwarding fediverse paths to $upstream"
