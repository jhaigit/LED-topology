"""/api/status reports the running version, so a deploy can be checked
from the API rather than only from the page footer."""

from unittest.mock import MagicMock

from ltp_controller import __version__
from ltp_controller.web import app as web_app


def _client(monkeypatch, revision):
    monkeypatch.setattr(web_app, "_git_revision", lambda: revision)
    controller = MagicMock(sources=[], online_sources=[], sinks=[], online_sinks=[], fleets=[])
    router = MagicMock(routes=[], active_routes=[])
    return web_app.create_app(controller=controller, router=router).test_client()


def test_status_reports_version_and_revision(monkeypatch):
    data = _client(monkeypatch, "a38d662").get("/api/status").get_json()
    assert data["version"] == __version__
    assert data["git_revision"] == "a38d662"
    assert data["sinks"] == {"total": 0, "online": 0}


def test_status_revision_null_outside_git_checkout(monkeypatch):
    data = _client(monkeypatch, None).get("/api/status").get_json()
    assert data["git_revision"] is None


def test_footer_and_status_agree(monkeypatch):
    client = _client(monkeypatch, "a38d662-dirty")
    assert client.get("/api/status").get_json()["git_revision"] == "a38d662-dirty"
    assert b"(a38d662-dirty)" in client.get("/").data
