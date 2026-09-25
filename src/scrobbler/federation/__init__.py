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
    # Tables are always part of the schema (migrations run regardless), so the models
    # are always registered; "off" means no routes, receivers or tasks.
    from scrobbler.federation import models  # noqa: F401

    config = FederationConfig.from_mapping(app.config)
    app.extensions["federation"] = config
    if not config.enabled:
        return
    log.info("federation enabled: handles @user@%s, actors at %s", config.domain, config.base_url)

    from scrobbler.api import get_api
    from scrobbler.federation import api, openapi, web

    app.register_blueprint(web.bp)
    core_api = get_api(app)
    core_api.register_blueprint(api.blp, url_prefix="/api/v1/federation")
    openapi.register(app, core_api)

    from scrobbler import worker
    from scrobbler.federation import delivery, inbox

    worker.register_task(app, "federation.inbox", inbox.work_once)
    worker.register_task(app, "federation.deliver", delivery.work_once)
    worker.register_periodic(app, "federation.inbox.maintenance", 300, inbox.maintenance)
    worker.register_periodic(app, "federation.gauges", 30, delivery.maintenance)
