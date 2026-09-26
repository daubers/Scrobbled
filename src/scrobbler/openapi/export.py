"""Write docs/openapi.json: the API as documented with every optional module enabled.

    uv run python -m scrobbler.openapi.export docs/openapi.json

A running server's own /api/openapi.json only lists what it has enabled.
"""

import json
import sys

from scrobbler.config import Config


class DocsConfig(Config):
    START_METRICS_SERVER = False
    UI_BASE_URL = "https://scrobble.example"
    FEDERATION_ENABLED = "1"
    FEDERATION_DOMAIN = "scrobble.example"
    FEDERATION_BASE_URL = "https://scrobble.example"
    FEDERATION_KEY_SECRET = "documentation-only-not-a-real-secret-0000"


def build_spec() -> dict:
    from scrobbler import create_app
    from scrobbler.api import get_api

    app = create_app(DocsConfig)
    with app.app_context():
        return get_api(app).spec.to_dict()


def main(argv: list[str]) -> None:
    path = argv[1] if len(argv) > 1 else "docs/openapi.json"
    with open(path, "w") as out:
        json.dump(build_spec(), out, indent=2, ensure_ascii=False)
        out.write("\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main(sys.argv)
