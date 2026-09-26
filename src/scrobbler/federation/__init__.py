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
    from scrobbler.federation.cli import federation_cli

    app.register_blueprint(web.bp)
    core_api = get_api(app)
    core_api.register_blueprint(api.blp, url_prefix="/api/v1/federation")
    openapi.register(app, core_api)
    app.cli.add_command(federation_cli)

    from scrobbler import events, worker
    from scrobbler.federation import delivery, inbox
    from scrobbler.federation.publishing import milestones, weekly

    worker.register_task(app, "federation.inbox", inbox.work_once)
    worker.register_task(app, "federation.deliver", delivery.work_once)
    worker.register_task(app, "federation.milestones", milestones.work_once)
    worker.register_periodic(app, "federation.inbox.maintenance", 300, inbox.maintenance)
    worker.register_periodic(app, "federation.gauges", 30, delivery.maintenance)
    worker.register_periodic(app, "federation.weekly", 900, weekly.check_all)

    # blinker signals are process-wide, not per-app: once any app in a process enables
    # federation these stay connected for every app in it, including one built with
    # federation off (see test_federation_boundary.py for why "off" is instead enforced
    # by each receiver checking its own app's config). weak=False because the receivers
    # are plain module functions meant to live as long as the process does.
    events.scrobbles_stored.connect(milestones.on_scrobbles_stored, weak=False)
    events.import_finished.connect(milestones.on_import_finished, weak=False)
