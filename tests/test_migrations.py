import pytest
from alembic.script import ScriptDirectory
from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from scrobbler.extensions import db


@pytest.fixture
def scripts(app):
    return ScriptDirectory(app.extensions["migrate"].directory)


def applied() -> set[str]:
    return set(db.session.execute(text("SELECT version_num FROM alembic_version")).scalars())


def head_of(scripts: ScriptDirectory, branch: str) -> str:
    return scripts.get_revision(f"{branch}@head").revision


def test_there_are_exactly_two_branches(scripts):
    labels = {label for rev in scripts.walk_revisions() for label in rev.branch_labels}
    assert labels == {"core", "federation"}
    assert len(scripts.get_heads()) == 2


def test_both_branches_are_applied(app, scripts):
    assert applied() == {head_of(scripts, "core"), head_of(scripts, "federation")}


def test_federation_branch_can_be_removed_without_touching_core(app, scripts, user):
    core, federation = head_of(scripts, "core"), head_of(scripts, "federation")
    try:
        db.session.commit()
        downgrade(revision="federation@base")
        assert applied() == {core}
        assert db.session.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
    finally:
        db.session.rollback()
        upgrade(revision="heads")
    assert applied() == {core, federation}
