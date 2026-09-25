import hashlib

from scrobbler.lastfm.signature import is_valid, sign


def test_signature_sorts_params_and_appends_secret():
    params = {"token": "tok", "method": "auth.getSession", "api_key": "key"}
    expected = hashlib.md5(b"api_keykeymethodauth.getSessiontokentokSECRET").hexdigest()
    assert sign(params, "SECRET") == expected


def test_format_callback_and_api_sig_are_not_signed():
    base = {"method": "track.scrobble", "api_key": "key"}
    extra = {**base, "format": "json", "callback": "cb", "api_sig": "whatever"}
    assert sign(base, "s") == sign(extra, "s")


def test_signature_uses_utf8():
    params = {"artist": "Sigur Rós", "api_key": "k"}
    expected = hashlib.md5("api_keykartistSigur RósS".encode()).hexdigest()
    assert sign(params, "S") == expected


def test_is_valid_accepts_uppercase_hex_and_rejects_missing():
    params = {"api_key": "k", "method": "m"}
    sig = sign(params, "s")
    assert is_valid({**params, "api_sig": sig.upper()}, "s")
    assert not is_valid(params, "s")
    assert not is_valid({**params, "api_sig": sig}, "other-secret")
