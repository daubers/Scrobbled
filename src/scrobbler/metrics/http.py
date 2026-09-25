import time

from flask import Flask, g, request

from scrobbler import metrics


def _endpoint() -> str:
    return request.url_rule.endpoint if request.url_rule else "unmatched"


def init_app(app: Flask) -> None:
    @app.before_request
    def _start_timer():
        g._metrics_endpoint = _endpoint()
        g._metrics_start = time.perf_counter()
        metrics.http_requests_in_progress.labels(endpoint=g._metrics_endpoint).inc()

    @app.after_request
    def _record(response):
        endpoint = g.get("_metrics_endpoint", _endpoint())
        start = g.get("_metrics_start")
        if start is not None:
            metrics.http_request_duration_seconds.labels(
                endpoint=endpoint, http_method=request.method
            ).observe(time.perf_counter() - start)
        metrics.http_requests_total.labels(
            endpoint=endpoint, http_method=request.method, status=str(response.status_code)
        ).inc()
        return response

    @app.teardown_request
    def _finish(_exc):
        endpoint = g.pop("_metrics_endpoint", None)
        if endpoint is not None:
            metrics.http_requests_in_progress.labels(endpoint=endpoint).dec()
