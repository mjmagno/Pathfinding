"""RiotClient error handling, without touching the network."""

import io
import urllib.error

import pytest

from app import riot


def _fake_urlopen(status, body, seen):
    def urlopen(request, timeout=None):
        seen.append(request)
        raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(body.encode()))
    return urlopen


def _client():
    return riot.RiotClient("RGAPI-test", "americas", max_retries=0)


def test_sends_user_agent(monkeypatch):
    # Cloudflare blocks Python's default "Python-urllib" User-Agent with 403 / 1010.
    seen = []
    monkeypatch.setattr(riot.urllib.request, "urlopen", _fake_urlopen(404, "{}", seen))
    with pytest.raises(riot.RiotApiError):
        _client().get_match("NA1_1")
    assert seen[0].get_header("User-agent") == riot.USER_AGENT


def test_riot_403_is_reported_as_key_problem(monkeypatch):
    body = '{"status": {"message": "Forbidden", "status_code": 403}}'
    monkeypatch.setattr(riot.urllib.request, "urlopen", _fake_urlopen(403, body, []))
    with pytest.raises(riot.RiotApiError, match="rejected the key"):
        _client().get_match("NA1_1")


@pytest.mark.parametrize("body", ["error code: 1010", "<html><title>Attention Required!</title></html>", ""])
def test_non_riot_403_is_not_blamed_on_key(monkeypatch, body):
    monkeypatch.setattr(riot.urllib.request, "urlopen", _fake_urlopen(403, body, []))
    with pytest.raises(riot.RiotApiError) as info:
        _client().get_match("NA1_1")
    assert "not an API key problem" in str(info.value)
    assert "rejected the key" not in str(info.value)
