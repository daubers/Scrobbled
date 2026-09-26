"""Posts to followers: weekly summaries (weekly.py) and milestones (milestones.py), and
later now-playing. Shared machinery — idempotent publishing, addressing, fan-out and
deletion — lives in base.py.
"""

from scrobbler.federation.publishing.base import delete_all_for, delete_post, publish

__all__ = ["delete_all_for", "delete_post", "publish"]
