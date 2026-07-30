"""Authorization URLs stay valid when the endpoint already has a query.

Regression coverage for MCP servers whose authorization server publishes an
``authorization_endpoint`` that carries query parameters (Railway:
``https://backboard.railway.com/oauth/auth?resource=https://backboard.railway.com``).
The SDK appends its own parameters with a bare ``?``, producing two of them, and
the provider then sees a request with no ``client_id``/``state``/PKCE.
"""

import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.mcp_oauth import _normalize_authorization_url, _redirect_handler

SDK_PARAMS = (
    "response_type=code"
    "&client_id=rlwy_oaci_abc123"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A40903%2Fcallback"
    "&state=st-1"
    "&code_challenge=chal-1"
    "&code_challenge_method=S256"
    "&resource=https%3A%2F%2Fmcp.railway.com"
    "&scope=openid+offline_access"
)


def _params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query, keep_blank_values=True)


def test_endpoint_query_does_not_swallow_sdk_params():
    # Exactly what the SDK hands the redirect handler for Railway.
    url = (
        "https://backboard.railway.com/oauth/auth"
        "?resource=https%3A%2F%2Fbackboard.railway.com"
        f"?{SDK_PARAMS}"
    )
    params = _params(_normalize_authorization_url(url))

    assert params["client_id"] == ["rlwy_oaci_abc123"]
    assert params["state"] == ["st-1"]
    assert params["code_challenge"] == ["chal-1"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == ["http://127.0.0.1:40903/callback"]
    assert params["scope"] == ["openid offline_access"]


def test_sdk_resource_wins_over_endpoint_resource():
    url = (
        "https://backboard.railway.com/oauth/auth"
        "?resource=https%3A%2F%2Fbackboard.railway.com"
        f"?{SDK_PARAMS}"
    )
    # RFC 8707: the resource must name the MCP server, not the auth server.
    assert _params(_normalize_authorization_url(url))["resource"] == [
        "https://mcp.railway.com"
    ]


def test_endpoint_extras_are_preserved():
    url = (
        "https://auth.example.com/authorize?tenant=acme&prompt=consent"
        f"?{SDK_PARAMS}"
    )
    params = _params(_normalize_authorization_url(url))
    assert params["tenant"] == ["acme"]
    assert params["prompt"] == ["consent"]
    assert params["client_id"] == ["rlwy_oaci_abc123"]


def test_well_formed_url_is_left_intact():
    url = f"https://auth.example.com/authorize?{SDK_PARAMS}"
    before = _params(url)
    after = _params(_normalize_authorization_url(url))
    assert after == before
    assert urlsplit(_normalize_authorization_url(url)).path == "/authorize"


def test_normalization_is_idempotent():
    url = (
        "https://backboard.railway.com/oauth/auth"
        "?resource=https%3A%2F%2Fbackboard.railway.com"
        f"?{SDK_PARAMS}"
    )
    once = _normalize_authorization_url(url)
    assert _normalize_authorization_url(once) == once


@pytest.mark.parametrize(
    "url",
    [
        "https://auth.example.com/authorize",
        "https://auth.example.com/authorize?",
    ],
)
def test_query_less_urls_survive(url):
    assert _normalize_authorization_url(url).startswith(
        "https://auth.example.com/authorize"
    )


def test_redirect_handler_prints_the_repaired_url(monkeypatch, capsys):
    monkeypatch.setattr("tools.mcp_oauth._can_open_browser", lambda: False)
    url = (
        "https://backboard.railway.com/oauth/auth"
        "?resource=https%3A%2F%2Fbackboard.railway.com"
        f"?{SDK_PARAMS}"
    )
    asyncio.run(_redirect_handler(url))

    printed = capsys.readouterr().err
    # One '?' in the URL the user is told to open, and the code params present.
    line = next(l.strip() for l in printed.splitlines() if "backboard.railway.com" in l)
    assert line.count("?") == 1
    assert "client_id=rlwy_oaci_abc123" in line
    assert "code_challenge=chal-1" in line
