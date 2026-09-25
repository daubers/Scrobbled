from flask_smorest import Blueprint, abort
from marshmallow import Schema, fields
from sqlalchemy import text

from scrobbler.extensions import db
from scrobbler.schemas import ErrorSchema

blp = Blueprint("Health", __name__, description="Liveness for load balancers and containers")


class HealthSchema(Schema):
    status = fields.String(metadata={"example": "ok"})


@blp.route("/healthz", methods=["GET"])
@blp.response(200, HealthSchema)
@blp.alt_response(503, schema=ErrorSchema, description="The database is unreachable")
def healthz():
    """Health check

    Returns 200 when the API can reach its database.
    """
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        db.session.rollback()
        abort(503, code="database_unavailable", message="The database is unreachable")
    return {"status": "ok"}
