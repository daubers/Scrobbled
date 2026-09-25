import time
from contextlib import contextmanager

from scrobbler import events
from scrobbler.services import imports, scrobbles
from scrobbler.services.scrobbles import TrackInput


@contextmanager
def capture(signal):
    received = []

    def receiver(sender, **kwargs):
        received.append((sender, kwargs))

    signal.connect(receiver)
    try:
        yield received
    finally:
        signal.disconnect(receiver)


def track(name, seconds_ago=60, artist="Artist"):
    return TrackInput(artist=artist, track=name, timestamp=int(time.time()) - seconds_ago)


def test_scrobbles_stored_carries_only_new_plays(app, user):
    first = track("One")
    scrobbles.submit_scrobbles(user, None, [first])
    with capture(events.scrobbles_stored) as received:
        scrobbles.submit_scrobbles(
            user,
            None,
            [first, track("Two"), TrackInput(artist="", track="x", timestamp=int(time.time()))],
        )
    [(sender, kwargs)] = received
    assert sender.id == user.id
    assert kwargs["source"] == "scrobble"
    assert [s.track for s in kwargs["scrobbles"]] == ["Two"]


def test_nothing_is_sent_when_nothing_new_is_stored(app, user):
    old = track("One")
    scrobbles.submit_scrobbles(user, None, [old])
    with capture(events.scrobbles_stored) as received:
        scrobbles.submit_scrobbles(user, None, [old])
    assert received == []


def test_now_playing_started_and_cleared(app, user):
    with capture(events.now_playing_changed) as received:
        scrobbles.update_now_playing(user, TrackInput(artist="A", track="Song"))
        scrobbles.submit_scrobbles(user, None, [track("Song", artist="A")])
    assert [kwargs["track"].track if kwargs["track"] else None for _, kwargs in received] == [
        "Song",
        None,
    ]


def test_imports_announce_stored_plays_and_completion(app, user):
    when = time.strftime("%d %b %Y %H:%M", time.gmtime(time.time() - 86400 * 400))
    csv = f"Artist,,One,{when}\nArtist,,One,{when}\nArtist,,Two,{when}\n"
    job = imports.create_file_import(user, "export.csv", csv.encode())
    with capture(events.scrobbles_stored) as stored, capture(events.import_finished) as finished:
        imports.run_worker(once=True)
    [(_, kwargs)] = stored
    assert kwargs["source"] == "import"
    assert sorted(s.track for s in kwargs["scrobbles"]) == ["One", "Two"]  # the repeat once
    [(sender, done)] = finished
    assert sender.id == user.id
    assert (done["job"].id, done["job"].status) == (job.id, "completed")


def test_cancelling_an_import_announces_it(app, user):
    job = imports.create_file_import(user, "export.csv", b"A,,T,1600000000\n")
    with capture(events.import_finished) as finished:
        imports.cancel_job(user, job.id)
    assert finished[0][1]["job"].status == "cancelled"


def test_a_failing_receiver_does_not_break_scrobbling(app, user):
    def broken(sender, **kwargs):
        raise RuntimeError("listener bug")

    events.scrobbles_stored.connect(broken)
    try:
        results = scrobbles.submit_scrobbles(user, None, [track("Still stored")])
    finally:
        events.scrobbles_stored.disconnect(broken)
    assert [r.status for r in results] == ["accepted"]
