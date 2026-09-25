from flask import current_app, g
from flask_smorest import Blueprint, abort

from scrobbler.api.decorators import authenticated
from scrobbler.schemas import (
    ErrorSchema,
    ImportFileSchema,
    ImportFormatSchema,
    ImportJobSchema,
    ImportOptionsSchema,
    LastfmImportSchema,
)
from scrobbler.services import imports

blp = Blueprint(
    "Imports",
    __name__,
    description=(
        "Import listening history from a Last.fm export file, or straight from Last.fm. "
        "Imports run in the background; poll a job to follow its progress."
    ),
)


@blp.route("/options", methods=["GET"])
@authenticated(blp)
@blp.response(200, ImportOptionsSchema)
def options():
    """What can be imported

    Pulling from Last.fm is only offered when the server has a Last.fm API key.
    """
    sources = ["csv", "json"] + (["lastfm"] if imports.lastfm_pull_available() else [])
    return {"sources": sources, "max_upload_bytes": current_app.config["IMPORT_MAX_BYTES"]}


@blp.route("/file", methods=["POST"])
@authenticated(blp)
@blp.arguments(ImportFileSchema, location="files")
@blp.arguments(ImportFormatSchema, location="query")
@blp.response(202, ImportJobSchema)
@blp.alt_response(400, schema=ErrorSchema, description="Empty or unsupported file")
@blp.alt_response(413, schema=ErrorSchema, description="File too large")
def import_file(files, query):
    """Import an export file

    Accepts lastfm-to-csv CSV files, other CSV/TSV files with a header row, and JSON
    exports of user.getRecentTracks pages. Files may be gzipped.
    """
    upload = files["file"]
    raw = upload.read(current_app.config["IMPORT_MAX_BYTES"] + 1)
    if len(raw) > current_app.config["IMPORT_MAX_BYTES"]:
        abort(413, code="file_too_large", message="That file is larger than this server accepts.")
    try:
        return imports.create_file_import(g.user, upload.filename, raw, query["format"])
    except imports.ImportFailed as err:
        abort(400, code=err.code, message=err.message)


@blp.route("/lastfm", methods=["POST"])
@authenticated(blp)
@blp.arguments(LastfmImportSchema)
@blp.response(202, ImportJobSchema)
@blp.alt_response(400, schema=ErrorSchema, description="Last.fm import isn't configured")
def import_lastfm(body):
    """Import from Last.fm

    Copies a Last.fm user's public scrobble history into your account.
    """
    try:
        return imports.create_lastfm_import(g.user, body["username"])
    except imports.ImportFailed as err:
        abort(400, code=err.code, message=err.message)


@blp.route("", methods=["GET"])
@authenticated(blp)
@blp.response(200, ImportJobSchema(many=True))
def list_imports():
    """Your recent imports"""
    return imports.list_jobs(g.user)


@blp.route("/<int:job_id>", methods=["GET"])
@authenticated(blp)
@blp.response(200, ImportJobSchema)
@blp.alt_response(404, schema=ErrorSchema, description="No such import")
def get_import(job_id):
    """One import's progress"""
    job = imports.get_job(g.user, job_id)
    if job is None:
        abort(404, code="not_found", message="No such import")
    return job


@blp.route("/<int:job_id>/cancel", methods=["POST"])
@authenticated(blp)
@blp.response(200, ImportJobSchema)
@blp.alt_response(404, schema=ErrorSchema, description="No such import")
def cancel_import(job_id):
    """Cancel an import

    Stops a waiting or running import. Scrobbles already imported are kept.
    """
    job = imports.cancel_job(g.user, job_id)
    if job is None:
        abort(404, code="not_found", message="No such import")
    return job
