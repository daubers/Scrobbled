"""Fediverse admin commands (`flask federation ...`)."""

import click
from flask.cli import AppGroup, with_appcontext

federation_cli = AppGroup("federation", help="Fediverse admin commands.")


@federation_cli.command("post-weekly")
@click.option("--user", "username", required=True, help="Username to post for.")
@click.option(
    "--now",
    "immediately",
    is_flag=True,
    required=True,
    help="Post about the week still in progress right now, ignoring the Monday-09:00 "
    "gate (the only mode this command supports, for operators and testing).",
)
@with_appcontext
def post_weekly(username: str, immediately: bool) -> None:
    """Post one user's weekly summary immediately."""
    from scrobbler.extensions import db
    from scrobbler.federation.publishing import weekly
    from scrobbler.services import accounts

    user = accounts.find_user(username)
    if user is None:
        raise click.ClickException(f"No such user: {username}")
    result = weekly.post_now(user.id)
    db.session.commit()
    if result == "posted":
        click.echo(f"Posted a weekly summary for {username}.")
    elif result == "already_posted":
        click.echo(f"{username} already has a post for this week.")
    elif result == "skipped_empty":
        click.echo(f"{username} hasn't scrobbled anything this week.")
    else:
        raise click.ClickException(f"{username} isn't sharing weekly summaries.")
