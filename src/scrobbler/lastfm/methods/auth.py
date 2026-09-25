from scrobbler.lastfm import errors
from scrobbler.lastfm.errors import LastFMError
from scrobbler.lastfm.registry import Call, Param, lastfm_method
from scrobbler.lastfm.responses import El
from scrobbler.services import accounts


def _session_el(session) -> El:
    return El.of(
        "session",
        El.text_el("name", session.user.username),
        El.text_el("key", session.session_key),
        El.text_el("subscriber", 0),
    )


@lastfm_method(
    "auth.getMobileSession",
    summary="Create a session key from a username and password",
    signed=True,
    params=(
        Param("username", "Username", required=True, example="alice"),
        Param("password", "Password (send over HTTPS, as a POST body)", required=True),
    ),
    notes=(
        "The legacy `authToken` (md5 of username + md5(password)) form is not supported, "
        "because it requires storing unsalted password hashes. Clients that only support "
        "it (such as pylast's `password_hash`) should use the desktop flow instead."
    ),
    example_xml=(
        '<lfm status="ok"><session><name>alice</name><key>d580d57f32848f5dcf574d1ce18d78b2</key>'
        "<subscriber>0</subscriber></session></lfm>"
    ),
)
def get_mobile_session(call: Call) -> El:
    user = accounts.authenticate(call.params["username"], call.params["password"])
    if user is None:
        raise LastFMError(errors.AUTHENTICATION_FAILED, "Invalid username or password")
    return _session_el(accounts.create_session(user, call.api_app, flow="mobile"))


@lastfm_method(
    "auth.getToken",
    summary="Fetch an unapproved request token (desktop auth flow, step 1)",
    signed=True,
    notes=(
        "Send the user to `/api/auth/?api_key=…&token=…` to approve the token, then call "
        "auth.getSession. Tokens expire after 60 minutes."
    ),
    example_xml='<lfm status="ok"><token>cf45fe5a3e3cebe168480a086d7fe481</token></lfm>',
)
def get_token(call: Call) -> El:
    return El.text_el("token", accounts.create_auth_token(call.api_app))


@lastfm_method(
    "auth.getSession",
    summary="Exchange an approved token for a session key (desktop auth flow, step 3)",
    signed=True,
    params=(Param("token", "Token from auth.getToken, approved by the user", required=True),),
    example_xml=(
        '<lfm status="ok"><session><name>alice</name><key>d580d57f32848f5dcf574d1ce18d78b2</key>'
        "<subscriber>0</subscriber></session></lfm>"
    ),
)
def get_session(call: Call) -> El:
    try:
        session = accounts.exchange_auth_token(call.api_app, call.params["token"])
    except accounts.AuthTokenError as err:
        code = {
            "expired": errors.TOKEN_EXPIRED,
            "unauthorized": errors.UNAUTHORIZED_TOKEN,
        }.get(err.reason, errors.AUTHENTICATION_FAILED)
        message = "Invalid authentication token supplied" if err.reason == "invalid" else None
        raise LastFMError(code, message) from err
    return _session_el(session)
