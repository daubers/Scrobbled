import json
import re
from pathlib import Path

import pytest
import yaml
from prometheus_client.metrics import MetricWrapperBase

from scrobbler import metrics
from scrobbler.federation import metrics as federation_metrics

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"
DASHBOARDS = sorted((DEPLOY / "grafana" / "dashboards").glob("*.json"))
METRIC_NAME = re.compile(r"\bscrobbler_[a-z0-9_]+")


def defined_series() -> set[str]:
    """Every series name the service can export."""
    names = set(metrics.COLLECTED.values())
    modules = (metrics, federation_metrics)
    for value in (v for module in modules for v in vars(module).values()):
        if not isinstance(value, MetricWrapperBase):
            continue
        name, kind = value._name, value._type
        if kind == "counter":
            names |= {f"{name}_total", f"{name}_created"}
        elif kind == "histogram":
            names |= {f"{name}_bucket", f"{name}_sum", f"{name}_count", f"{name}_created"}
        else:
            names.add(name)
    return names


def exprs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("expr", "query", "definition") and isinstance(value, str):
                yield value
            else:
                yield from exprs(value)
    elif isinstance(node, list):
        for item in node:
            yield from exprs(item)


def test_the_expected_dashboards_exist():
    assert {p.stem for p in DASHBOARDS} == {
        "api-overview",
        "lastfm-api",
        "usage",
        "database",
        "federation",
    }


def test_dashboard_uids_are_unique():
    uids = [json.loads(p.read_text())["uid"] for p in DASHBOARDS]
    assert len(uids) == len(set(uids))


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_dashboard_queries_only_use_defined_metrics(path):
    board = json.loads(path.read_text())
    used = {name for expr in exprs(board) for name in METRIC_NAME.findall(expr)}
    assert used, f"{path.name} queries no scrobbler metrics"
    assert used <= defined_series(), f"undefined metrics: {sorted(used - defined_series())}"


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_panels_use_the_provisioned_datasource(path):
    board = json.loads(path.read_text())
    for panel in board["panels"]:
        if panel["type"] != "row":
            assert panel["datasource"]["uid"] == "prometheus", panel["title"]


def test_alert_rules_only_use_defined_metrics():
    rules = yaml.safe_load((DEPLOY / "prometheus" / "alerts.yml").read_text())
    used = {
        name
        for group in rules["groups"]
        for rule in group["rules"]
        for name in METRIC_NAME.findall(rule["expr"])
    }
    assert used <= defined_series(), f"undefined metrics: {sorted(used - defined_series())}"


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.stem)
def test_queries_have_no_invalid_string_escapes(path):
    """PromQL rejects escapes like \\. inside double-quoted strings (Grafana would show a
    query error); use a character class such as [.] instead."""
    invalid = re.compile(r'(?<!\\)\\[^\\"nrtabfvxuU0-7]')
    for expr in exprs(json.loads(path.read_text())):
        assert not invalid.search(expr), f"invalid escape in {path.name}: {expr}"
