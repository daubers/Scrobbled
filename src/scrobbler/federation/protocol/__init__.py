"""Pure ActivityPub protocol code: vocabulary builders, addressing, WebFinger and
NodeInfo documents, HTTP signature schemes.

Rule: nothing in this package imports Flask, SQLAlchemy, Werkzeug or any other part of
Scrobbler; only the standard library and `cryptography`. Spec changes land here and are
tested with spec test vectors alone. (Enforced by tests/test_federation_boundary.py.)
"""
