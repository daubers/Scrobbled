"""HTTP signatures: published test vectors, round trips, and tampering."""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from scrobbler.federation.protocol import signatures
from scrobbler.federation.protocol.signatures import SignatureError, SignedRequest
from scrobbler.federation.protocol.signatures.draft_cavage import DraftCavage
from scrobbler.federation.protocol.signatures.rfc9421 import Rfc9421, parse_dictionary

DATA = Path(__file__).parent / "data"


def load_key(name):
    return serialization.load_pem_private_key((DATA / name).read_bytes(), password=None)


def keys_by_id(**keys):
    return lambda key_id: keys[key_id].public_key() if key_id in keys else None


# --- Published test vectors ---------------------------------------------------------------

CAVAGE_KEY = load_key("draft-cavage-test-key.pem")
CAVAGE_REQUEST = SignedRequest(
    method="POST",
    url="https://example.com/foo?param=value&pet=dog",
    headers={
        "Host": "example.com",
        "Date": "Sun, 05 Jan 2014 21:31:40 GMT",
        "Content-Type": "application/json",
        "Digest": "SHA-256=X48E9qOokqqrvdts8nOJRJN3OWDUoyWxBf7kbu9DBPE=",
        "Content-Length": "18",
    },
    body=b'{"hello": "world"}',
)
CAVAGE_NOW = datetime(2014, 1, 5, 21, 31, 40, tzinfo=UTC)


def cavage(signature_header):
    return dataclasses.replace(
        CAVAGE_REQUEST, headers={**CAVAGE_REQUEST.headers, "Signature": signature_header}
    )


def test_draft_cavage_default_test_vector():
    """draft-cavage-12, appendix C.1: only the date signed."""
    request = cavage(
        'keyId="Test",algorithm="rsa-sha256",signature="SjWJWbWN7i0wzBvtPl8rbASWz5xQW6mcJmn+'
        "ibttBqtifLN7Sazz6m79cNfwwb8DMJ5cou1s7uEGKKCs+FLEEaDV5lp7q25WqS+lavg7T8hc0GppauB6hbg"
        'EKTwblDHYGEtbGmtdHgVCk9SuS13F0hZ8FD0k/5OxEPXe5WozsbM="'
    )
    verified = DraftCavage().verify(
        request, keys_by_id(Test=CAVAGE_KEY), CAVAGE_NOW, require=("date",)
    )
    assert (verified.scheme, verified.key_id) == ("draft-cavage", "Test")


def test_draft_cavage_basic_test_vector():
    """draft-cavage-12, appendix C.2: (request-target), host and date."""
    request = cavage(
        'keyId="Test",algorithm="rsa-sha256",headers="(request-target) host date",'
        'signature="qdx+H7PHHDZgy4y/Ahn9Tny9V3GP6YgBPyUXMmoxWtLbHpUnXS2mg2+SbrQDMCJypxBLSPQR2aAjn7'
        "ndmw2iicw3HMbe8VfEdKFYRqzic+efkb3nndiv/x1xSHDJWeSWkx3ButlYSuBskLu6kd9Fswtemr3lgdDEmn0"
        '4swr2Os0="'
    )
    require = ("(request-target)", "host", "date")
    verified = DraftCavage().verify(
        request, keys_by_id(Test=CAVAGE_KEY), CAVAGE_NOW, require=require
    )
    assert verified.key_id == "Test"
    # ...but it's not enough for an ActivityPub POST, which must sign the digest
    with pytest.raises(SignatureError) as err:
        DraftCavage().verify(request, keys_by_id(Test=CAVAGE_KEY), CAVAGE_NOW)
    assert err.value.reason == "insufficient"


RFC_KEY = load_key("rfc9421-test-key-rsa.pem")


def test_rfc9421_rsa_v1_5_sha256_test_vector():
    """RFC 9421 §4.3: the proxy's rsa-v1_5-sha256 signature (test-key-rsa)."""
    request = SignedRequest(
        method="POST",
        url="https://origin.host.internal.example/foo?param=Value&Pet=dog",
        headers={
            "Host": "origin.host.internal.example",
            "Date": "Tue, 20 Apr 2021 02:07:56 GMT",
            "Content-Type": "application/json",
            "Content-Length": "18",
            "Forwarded": "for=192.0.2.123;host=example.com;proto=https",
            "Content-Digest": (
                "sha-512=:WZDPaVn/7XgHaAy8pmojAkGWoRx2UFChF41A2svX+TaPm+AbwAgBWnrIiYllu7BNNyealdVLv"
                "RwEmTHWXvJwew==:"
            ),
            "Signature-Input": (
                'proxy_sig=("@method" "@authority" "@path" "content-digest" "content-type" '
                '"content-length" "forwarded");created=1618884480;keyid="test-key-rsa";'
                'alg="rsa-v1_5-sha256";expires=1618884540'
            ),
            "Signature": (
                "proxy_sig=:S6ZzPXSdAMOPjN/6KXfXWNO/f7V6cHm7BXYUh3YD/fRad4BCaRZxP+JH+8XY1I6+8Cy+CM5"
                "g92iHgxtRPz+MjniOaYmdkDcnL9cCpXJleXsOckpURl49GwiyUpZ10KHgOEe11sx3G2gxI8S0jnxQB+Pu68"
                "U9vVcasqOWAEObtNKKZd8tSFu7LB5YAv0RAGhB8tmpv7sFnIm9y+7X5kXQfi8NMaZaA8i2ZHwpBdg7a6CM"
                "fwnnrtflzvZdXAsD3LH2TwevU+/PBPv0B6NMNk93wUs/vfJvye+YuI87HU38lZHowtznbLVdp770I6VHR"
                "6WfgS9ddzirrswsE1w5o0LV/g==:"
            ),
        },
        body=b'{"hello": "world"}',
    )
    now = datetime.fromtimestamp(1618884480, UTC)
    verified = Rfc9421().verify(
        request, keys_by_id(**{"test-key-rsa": RFC_KEY}), now, require=("@method", "@path")
    )
    assert verified.key_id == "test-key-rsa"
    # Past `expires` the same signature is refused
    with pytest.raises(SignatureError) as err:
        Rfc9421().verify(
            request,
            keys_by_id(**{"test-key-rsa": RFC_KEY}),
            now + timedelta(minutes=2),
            require=("@method", "@path"),
        )
    assert err.value.reason == "expired"


# --- Round trips with our own keys ---------------------------------------------------------

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_ID = "https://remote.example/users/bob#main-key"
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
LOOKUP = keys_by_id(**{KEY_ID: KEY})
BODY = b'{"type": "Follow", "actor": "https://remote.example/users/bob"}'
INBOX = "https://scrobble.example/users/alice/inbox"
SCHEMES = ["draft-cavage", "rfc9421"]


def signed(scheme, method="POST", url=INBOX, body=BODY, now=NOW, key=KEY):
    request = SignedRequest(method, url, {"Content-Type": "application/activity+json"}, body)
    added = signatures.sign(request, scheme, KEY_ID, key, now)
    return dataclasses.replace(request, headers={**request.headers, **added})


@pytest.mark.parametrize("scheme", SCHEMES)
def test_round_trip_post(scheme):
    request = signed(scheme)
    assert signatures.detect(request).name == scheme
    verified = signatures.verify(request, LOOKUP, NOW + timedelta(seconds=5))
    assert (verified.scheme, verified.key_id) == (scheme, KEY_ID)


@pytest.mark.parametrize("scheme", SCHEMES)
def test_round_trip_get(scheme):
    request = signed(scheme, method="GET", url="https://remote.example/users/bob", body=b"")
    assert signatures.verify(request, LOOKUP, NOW).scheme == scheme


def test_sent_headers_have_the_expected_shapes():
    cavage_headers = signed("draft-cavage").headers
    assert cavage_headers["Digest"].startswith("SHA-256=")
    assert 'headers="(request-target) host date digest"' in cavage_headers["Signature"]
    rfc_headers = signed("rfc9421").headers
    assert rfc_headers["Content-Digest"].startswith("sha-256=:")
    assert rfc_headers["Signature-Input"].startswith(
        'sig1=("@method" "@target-uri" "content-digest");created='
    )
    assert rfc_headers["Signature"].startswith("sig1=:") and rfc_headers["Signature"].endswith(":")


def tamper(request, **changes):
    headers = {**request.headers, **changes.pop("headers", {})}
    return dataclasses.replace(request, headers=headers, **changes)


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"body": b'{"type": "Follow", "actor": "https://evil.example/users/eve"}'},
            "digest_mismatch",
        ),
        ({"url": "https://scrobble.example/users/mallory/inbox"}, "bad_signature"),
        ({"method": "PUT"}, "bad_signature"),
    ],
)
def test_tampering_is_detected(scheme, change, reason):
    with pytest.raises(SignatureError) as err:
        signatures.verify(tamper(signed(scheme), **change), LOOKUP, NOW)
    assert err.value.reason == reason


def test_draft_cavage_date_tampering_is_detected():
    request = tamper(signed("draft-cavage"), headers={"Date": "Fri, 25 Sep 2026 12:00:01 GMT"})
    with pytest.raises(SignatureError) as err:
        signatures.verify(request, LOOKUP, NOW)
    assert err.value.reason == "bad_signature"


@pytest.mark.parametrize("scheme", SCHEMES)
@pytest.mark.parametrize("skew", [timedelta(hours=13), -timedelta(hours=13)])
def test_old_or_future_signatures_are_refused(scheme, skew):
    with pytest.raises(SignatureError) as err:
        signatures.verify(signed(scheme, now=NOW + skew), LOOKUP, NOW)
    assert err.value.reason == "expired"


@pytest.mark.parametrize("scheme", SCHEMES)
def test_wrong_key_is_refused(scheme):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(SignatureError) as err:
        signatures.verify(signed(scheme, key=other), LOOKUP, NOW)
    assert err.value.reason == "bad_signature"


@pytest.mark.parametrize("scheme", SCHEMES)
def test_unknown_key_is_reported(scheme):
    with pytest.raises(SignatureError) as err:
        signatures.verify(signed(scheme), lambda key_id: None, NOW)
    assert err.value.reason == "unknown_key"


def test_unsigned_request():
    with pytest.raises(SignatureError) as err:
        signatures.verify(SignedRequest("POST", INBOX, {}, BODY), LOOKUP, NOW)
    assert err.value.reason == "missing"


def test_rfc9421_must_cover_method_target_and_digest():
    request = signed("rfc9421")
    weak = tamper(
        request,
        headers={
            "Signature-Input": request.headers["Signature-Input"].replace(' "content-digest"', "")
        },
    )
    with pytest.raises(SignatureError) as err:
        signatures.verify(weak, LOOKUP, NOW)
    assert err.value.reason == "insufficient"


def test_draft_cavage_hs2019_is_accepted():
    request = signed("draft-cavage")
    hs2019 = tamper(
        request, headers={"Signature": request.headers["Signature"].replace("rsa-sha256", "hs2019")}
    )
    assert signatures.verify(hs2019, LOOKUP, NOW).scheme == "draft-cavage"


@pytest.mark.parametrize(
    "header",
    ['keyId="x"', "garbage", 'keyId="x",signature="!!!not base64!!!"'],
)
def test_malformed_draft_cavage_headers(header):
    request = SignedRequest(
        "GET", INBOX, {"Signature": header, "Date": "Fri, 25 Sep 2026 12:00:00 GMT"}
    )
    with pytest.raises(SignatureError) as err:
        DraftCavage().verify(request, LOOKUP, NOW, require=())
    assert err.value.reason in ("malformed", "unknown_key")


def test_structured_field_parsing():
    parsed = parse_dictionary('sig1=("@method" "@target-uri");created=1;keyid="k\\"q", sig2=:aGk=:')
    assert [i.value for i in parsed["sig1"].items] == ["@method", "@target-uri"]
    assert dict(parsed["sig1"].params) == {"created": 1, "keyid": 'k"q'}
    assert parsed["sig2"].value == b"hi"
    with pytest.raises(SignatureError):
        parse_dictionary('sig1=("unterminated')
