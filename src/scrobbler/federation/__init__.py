"""ActivityPub federation: optional, self-contained, replaceable.

The rest of Scrobbler only calls `init_app(app)` (from `create_app`) and sends domain
events (`scrobbler.events`). Everything else about federation lives in this package;
see README.md here and docs/design/activitypub.md.

With FEDERATION_ENABLED off (the default) `init_app` registers nothing: no routes, no
event receivers, no worker tasks.
"""

import logging

from flask import Flask

from scrobbler.federation.config import FederationConfig

log = logging.getLogger(__name__)


def init_app(app: Flask) -> None:
    config = FederationConfig.from_mapping(app.config)
    app.extensions["federation"] = config
    if not config.enabled:
        return
    log.info("federation enabled: handles @user@%s, actors at %s", config.domain, config.base_url)
    # Phase 1 onwards registers routes, event receivers and worker tasks here.
