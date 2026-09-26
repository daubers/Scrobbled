import gzip
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from werkzeug.serving import make_server

from scrobbler import worker
from scrobbler.extensions import db
from scrobbler.models import ImportJob, Scrobble
from scrobbler.services import imports
from scrobbler.services.imports import ImportFailed, parse_csv, parse_json, parse_time

NOW = datetime.now(UTC).replace(microsecond=0)


def ts(days_ago: float) -> int:
    return int((NOW - timedelta(days=days_ago)).timestamp())


def fmt(days_ago: float, pattern="%d %b %Y %H:%M") -> str:
    return (NOW - timedelta(days=days_ago)).strftime(pattern)


def stored(user):
    return db.session.scalars(
        db.select(Scrobble).filter_by(user_id=user.id).order_by(Scrobble.played_at.desc())
    ).all()


def run_all():
    worker.run(once=True)
    db.session.expire_all()


# --- Parsing ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1700000000", 1700000000),
        ("1700000000000", 1700000000),  # milliseconds
        ("14 Nov 2023 22:13", 1700000000 - 20),
        ("14 Nov 2023, 22:13", 1700000000 - 20),
        ("2023-11-14T22:13:20Z", 1700000000),
        ("2023-11-14 22:13:20", 1700000000),
        ("not a date", None),
        ("", None),
    ],
)
def test_parse_time(value, expected):
    assert parse_time(value) == expected


def test_lastfm_to_csv_without_header():
    text = f'Radiohead,In Rainbows,Reckoner,{fmt(400)}\n"Björk, Guðmundsdóttir",,Jóga,{fmt(1)}\n'
    first, second = parse_csv(text)
    assert (first.artist, first.album, first.track) == ("Radiohead", "In Rainbows", "Reckoner")
    assert first.timestamp == ts(400) - ts(400) % 60
    assert (second.artist, second.album) == ("Björk, Guðmundsdóttir", None)


def test_csv_with_header_semicolons_and_extra_columns():
    text = (
        "uts;utc_time;artist;artist_mbid;album;album_mbid;track;track_mbid\n"
        f"{ts(3)};x;Portishead;;Dummy;;Roads;9b3c1a2e-0000-0000-0000-000000000000\n"
        f"bad;x;Portishead;;Dummy;;Glory Box;\n"
    )
    good, bad = parse_csv(text)
    assert (good.artist, good.track, good.album, good.timestamp) == (
        "Portishead",
        "Roads",
        "Dummy",
        ts(3),
    )
    assert good.mbid == "9b3c1a2e-0000-0000-0000-000000000000"
    assert bad is None


def test_json_page_export_skips_now_playing():
    pages = [
        {
            "track": [
                {"artist": {"#text": "Nobody"}, "name": "Live", "@attr": {"nowplaying": "true"}},
                {
                    "artist": {"#text": "Radiohead", "mbid": ""},
                    "album": {"#text": "In Rainbows"},
                    "name": "Nude",
                    "date": {"uts": str(ts(2)), "#text": "ignored"},
                },
            ],
            "@attr": {"page": "1"},
        },
        {"track": {"artist": {"name": "Björk"}, "name": "Hunter", "date": {"uts": str(ts(5))}}},
    ]
    items = list(parse_json(json.dumps(pages)))
    assert items[0] is None
    assert [(i.artist, i.track) for i in items[1:]] == [("Radiohead", "Nude"), ("Björk", "Hunter")]


def test_json_recenttracks_response_and_flat_list():
    response = {
        "recenttracks": {"track": [{"artist": "A", "name": "T", "date": {"uts": str(ts(1))}}]}
    }
    assert [i.track for i in parse_json(json.dumps(response))] == ["T"]
    flat = [{"artist": "A", "track": "U", "timestamp": ts(1)}]
    assert [i.track for i in parse_json(json.dumps(flat))] == ["U"]


def test_json_errors():
    with pytest.raises(ImportFailed, match="valid JSON"):
        list(parse_json("{nope"))
    with pytest.raises(ImportFailed, match="Last.fm export"):
        list(parse_json('{"something": "else"}'))


def test_format_detection():
    assert imports.detect_format("export.json", b"x") == "json"
    assert imports.detect_format("scrobbles.csv", b"[") == "csv"
    assert imports.detect_format(None, b'  [{"a": 1}]') == "json"
    assert imports.detect_format("export", b"artist,album") == "csv"


# --- File jobs ---------------------------------------------------------------------------


def test_file_import_keeps_old_plays_and_dedupes(app, user, metric_delta):
    imported = metric_delta("scrobbler_imported_scrobbles_total", source="csv", result="imported")
    completed = metric_delta("scrobbler_imports_total", source="csv", status="completed")
    text = (
        f"Radiohead,In Rainbows,Reckoner,{fmt(900)}\n"  # years old: fine for an import
        f"Radiohead,In Rainbows,Reckoner,{fmt(900)}\n"  # repeated row
        f",No Artist,Track,{fmt(3)}\n"  # skipped
        f"Future,,Track,{fmt(-2)}\n"  # skipped: in the future
        f"Portishead,Dummy,Roads,not-a-date\n"  # skipped: no time
        f"Björk,Homogenic,Jóga,{fmt(1)}\n"
    )
    job = imports.create_file_import(user, "export.csv", text.encode())
    assert (job.source, job.status) == ("csv", "pending")
    run_all()

    job = db.session.get(ImportJob, job.id)
    assert job.status == "completed"
    assert (job.total, job.processed, job.imported, job.duplicates, job.skipped) == (6, 6, 2, 1, 3)
    assert job.payload is None
    assert [s.track for s in stored(user)] == ["Jóga", "Reckoner"]
    assert (imported.delta, completed.delta) == (2, 1)

    # Importing the same export again adds nothing
    again = imports.create_file_import(user, "export.csv", text.encode())
    run_all()
    again = db.session.get(ImportJob, again.id)
    assert (again.imported, again.duplicates) == (0, 3)
    assert len(stored(user)) == 2


def test_gzipped_json_upload(app, user):
    data = [{"artist": "A", "name": f"T{i}", "date": {"uts": str(ts(30 + i))}} for i in range(2500)]
    raw = gzip.compress(json.dumps(data).encode())
    job = imports.create_file_import(user, "export.json.gz", raw)
    run_all()
    job = db.session.get(ImportJob, job.id)
    assert (job.source, job.status, job.imported) == ("json", "completed", 2500)


def test_bad_file_fails_the_job_with_a_reason(app, user, metric_delta):
    failed = metric_delta("scrobbler_imports_total", source="json", status="failed")
    job = imports.create_file_import(user, "export.json", b'{"not": "an export"}')
    run_all()
    job = db.session.get(ImportJob, job.id)
    assert job.status == "failed"
    assert "Last.fm export" in job.error
    assert failed.delta == 1


def test_empty_upload_is_rejected(app, user):
    with pytest.raises(ImportFailed):
        imports.create_file_import(user, "empty.csv", b"  \n")


def test_cancelled_jobs_are_not_processed(app, user):
    job = imports.create_file_import(user, "export.csv", f"A,,T,{fmt(1)}\n".encode())
    imports.cancel_job(user, job.id)
    run_all()
    job = db.session.get(ImportJob, job.id)
    assert job.status == "cancelled"
    assert stored(user) == []


def test_stalled_jobs_are_requeued(app, user):
    job = imports.create_file_import(user, "export.csv", f"A,,T,{fmt(1)}\n".encode())
    job.status = "running"
    job.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    db.session.commit()
    assert imports.requeue_stalled() == 1
    run_all()
    assert db.session.get(ImportJob, job.id).status == "completed"


def test_jobs_are_private(app, user, make_user):
    job = imports.create_file_import(user, "export.csv", f"A,,T,{fmt(1)}\n".encode())
    other = make_user(username="mallory")
    assert imports.get_job(other, job.id) is None
    assert imports.cancel_job(other, job.id) is None


# --- Last.fm pull ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fake_lastfm(app):
    """This app's own Last.fm-compatible API stands in for last.fm."""
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/2.0/"
    server.shutdown()


@pytest.fixture
def lastfm_config(app, fake_lastfm, api_app):
    saved = {
        k: app.config[k] for k in ("LASTFM_API_KEY", "LASTFM_API_URL", "LASTFM_REQUEST_INTERVAL")
    }
    app.config.update(
        LASTFM_API_KEY=api_app.api_key, LASTFM_API_URL=fake_lastfm, LASTFM_REQUEST_INTERVAL=0
    )
    yield
    app.config.update(saved)


def test_lastfm_pull_needs_an_api_key(app, user):
    assert not imports.lastfm_pull_available()
    with pytest.raises(ImportFailed) as err:
        imports.create_lastfm_import(user, "someone")
    assert err.value.code == "lastfm_not_configured"


def test_lastfm_pull_pages_through_history(app, lastfm_config, user, make_user, metric_delta):
    remote = make_user(username="remote")
    db.session.add_all(
        Scrobble(
            user_id=remote.id,
            artist="Artist",
            track=f"Track {i}",
            played_at=NOW - timedelta(hours=i + 1),
        )
        for i in range(450)
    )
    db.session.commit()
    imported = metric_delta(
        "scrobbler_imported_scrobbles_total", source="lastfm", result="imported"
    )

    job = imports.create_lastfm_import(user, "remote")
    run_all()

    job = db.session.get(ImportJob, job.id)
    assert job.status == "completed", job.error
    assert (job.total, job.imported, job.next_page) == (450, 450, 4)  # 3 pages of 200
    assert len(stored(user)) == 450
    assert imported.delta == 450


def test_lastfm_pull_unknown_user(app, lastfm_config, user):
    job = imports.create_lastfm_import(user, "nobody-by-that-name")
    run_all()
    job = db.session.get(ImportJob, job.id)
    assert job.status == "failed"
    assert job.error == "No Last.fm user with that name."
