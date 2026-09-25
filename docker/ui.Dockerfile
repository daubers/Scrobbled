# Scrobbler web UI: static files served by nginx.
FROM nginx:1.29-alpine
ENV API_BASE_URL=http://localhost:5050
COPY deploy/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY docker/ui-config.sh /docker-entrypoint.d/40-scrobbler-config.sh
COPY frontend /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s CMD wget -qO- http://127.0.0.1/login.html >/dev/null || exit 1
