"""Domain events: how the core tells optional modules (such as federation) what happened.

The core sends these after committing, so receivers only ever see stored data. Modules
subscribe with `signal.connect(receiver)`; the core never imports them.

    scrobbles_stored     sender=User, scrobbles=[TrackInput, ...], source="scrobble" | "import"
                         Newly stored plays only: duplicates and ignored ones are left out.
    now_playing_changed  sender=User, track=TrackInput | None   (None: stopped or cleared)
    import_finished      sender=User, job=ImportJob             (completed, failed or cancelled)
"""

import logging

from blinker import Namespace, Signal

log = logging.getLogger(__name__)

_signals = Namespace()

scrobbles_stored = _signals.signal("scrobbles-stored")
now_playing_changed = _signals.signal("now-playing-changed")
import_finished = _signals.signal("import-finished")


def send(signal: Signal, sender, **kwargs) -> None:
    """Deliver to every receiver. A receiver that raises is logged and skipped: an
    optional module's bug must never fail a scrobble or an import."""
    for receiver in signal.receivers_for(sender):
        try:
            receiver(sender, **kwargs)
        except Exception:
            log.exception("receiver %r of %s failed", receiver, signal.name)
