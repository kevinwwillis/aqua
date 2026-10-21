import re
import threading
import time

import pytest
from fastapi.testclient import TestClient

from aqua.server import create_app

ASK = {
    "title": "Pick a player engine",
    "project": "/home/ada/player",
    "session_name": "Backup migration",
    "context": "# Context\n\nSee [the test page](https://example.org).",
    "questions": [
        {"header": "Engine", "question": "Which engine?", "options": [{"label": "hls.js"}, {"label": "native"}]},
        {"header": "Old rule", "question": "Drop the old rule?", "options": [{"label": "Yes"}, {"label": "No"}],
         "multiSelect": True},
    ],
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as c:
        yield c


def test_ask_list_answer_round_trip(client):
    q = client.post("/api/questions", json=ASK).json()
    assert q["status"] == "waiting" and q["blocking"] is True
    items = client.get("/api/questions?status=waiting").json()["items"]
    assert [i["id"] for i in items] == [q["id"]]
    r = client.post(f"/api/questions/{q['id']}/answer", json={"answers": ["hls.js", ["Yes"]], "note": "ok"})
    assert r.status_code == 200 and r.json()["answer"]["answers"] == ["hls.js", ["Yes"]]
    assert client.get("/api/questions?status=waiting").json()["items"] == []
    assert client.post(f"/api/questions/{q['id']}/answer", json={"answers": ["a", None]}).status_code == 409


def test_title_session_and_project_are_required(client):
    for field in ("title", "session_name", "project"):
        bad = dict(ASK, **{field: "   "})
        assert client.post("/api/questions", json=bad).status_code == 422
        missing = {k: v for k, v in ASK.items() if k != field}
        assert client.post("/api/questions", json=missing).status_code == 422


def test_answer_count_must_match_unless_dismissed(client):
    q = client.post("/api/questions", json=ASK).json()
    assert client.post(f"/api/questions/{q['id']}/answer", json={"answers": ["x"]}).status_code == 422
    r = client.post(f"/api/questions/{q['id']}/answer", json={"cancelled": True})
    assert r.status_code == 200 and r.json()["answer"]["cancelled"] is True


def test_withdraw(client):
    q = client.post("/api/questions", json=ASK).json()
    assert client.delete(f"/api/questions/{q['id']}").json()["status"] == "withdrawn"
    assert client.post(f"/api/questions/{q['id']}/answer", json={"answers": ["a", None]}).status_code == 409


def test_wait_returns_when_answered(client):
    q = client.post("/api/questions", json=ASK).json()

    def answer_later():
        time.sleep(0.5)
        client.post(f"/api/questions/{q['id']}/answer", json={"answers": ["native", None]})

    threading.Thread(target=answer_later).start()
    start = time.monotonic()
    got = client.get(f"/api/questions/{q['id']}/wait?wait=10").json()
    assert got["status"] == "answered" and time.monotonic() - start < 5


def test_wait_times_out_while_waiting(client):
    q = client.post("/api/questions", json=ASK).json()
    assert client.get(f"/api/questions/{q['id']}/wait?wait=0.3").json()["status"] == "waiting"


def test_changes_long_poll_wakes_on_new_question(client):
    v = client.get("/api/questions").json()["version"]
    threading.Thread(target=lambda: (time.sleep(0.3), client.post("/api/questions", json=ASK))).start()
    assert client.get(f"/api/changes?since={v}&wait=10").json()["version"] > v


def test_media_upload_and_type_check(client):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    r = client.post("/api/media", files={"file": ("shot.png", png, "image/png")})
    assert r.status_code == 201
    assert client.get(r.json()["url"]).content == png
    assert client.post("/api/media", files={"file": ("x.exe", b"MZ", "application/x-msdownload")}).status_code == 415
    assert client.get("/media/..%2Faqua.db").status_code == 404


def test_inbox_page_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<title>aqua</title>" in r.text


def test_svg_upload_is_refused_and_media_is_nosniff(client):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    assert client.post("/api/media", files={"file": ("x.svg", svg, "image/svg+xml")}).status_code == 415
    r = client.post("/api/media", files={"file": ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert client.get(r.json()["url"]).headers["x-content-type-options"] == "nosniff"


def test_every_waiting_question_is_listed(client):
    ids = [client.post("/api/questions", json=ASK).json()["id"] for _ in range(205)]
    listed = [i["id"] for i in client.get("/api/questions").json()["items"]]
    assert listed == ids


def test_ipad_install_files_are_served(client):
    assert client.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert client.get("/icons/icon-180.png").headers["content-type"] == "image/png"
    assert 'rel="apple-touch-icon"' in client.get("/").text


def test_set_aside_keeps_the_question_waiting(client):
    """Set aside is the user's list, not an answer: the agent keeps waiting and sees no change."""
    q = client.post("/api/questions", json=ASK).json()
    assert q["set_aside_at"] is None
    v = client.get("/api/questions").json()["version"]
    r = client.post(f"/api/questions/{q['id']}/aside", json={"aside": True})
    assert r.status_code == 200 and r.json()["set_aside_at"] and r.json()["status"] == "waiting"
    assert client.get("/api/questions").json()["version"] > v  # the inbox on other devices redraws
    listed = client.get("/api/questions?status=waiting").json()["items"]
    assert [i["id"] for i in listed] == [q["id"]] and listed[0]["set_aside_at"]
    assert client.get(f"/api/questions/{q['id']}/wait?wait=0.2").json()["status"] == "waiting"
    back = client.post(f"/api/questions/{q['id']}/aside", json={"aside": False}).json()
    assert back["set_aside_at"] is None


def test_a_set_aside_question_can_still_be_answered(client):
    q = client.post("/api/questions", json=ASK).json()
    client.post(f"/api/questions/{q['id']}/aside", json={"aside": True})
    r = client.post(f"/api/questions/{q['id']}/answer", json={"answers": ["hls.js", None]})
    assert r.status_code == 200 and r.json()["status"] == "answered"


def test_set_aside_only_applies_to_waiting_questions(client):
    q = client.post("/api/questions", json=ASK).json()
    client.delete(f"/api/questions/{q['id']}")
    assert client.post(f"/api/questions/{q['id']}/aside", json={"aside": True}).status_code == 409
    assert client.post("/api/questions/nope/aside", json={"aside": True}).status_code == 404


def test_old_database_gains_the_set_aside_column(tmp_path):
    """A database created before set aside existed still opens and gains the column."""
    import sqlite3

    from aqua.store import Store

    db = sqlite3.connect(tmp_path / "aqua.db")
    db.executescript(
        "CREATE TABLE questions (id TEXT PRIMARY KEY, created_at REAL NOT NULL, project TEXT NOT NULL,"
        " session_name TEXT NOT NULL, session_id TEXT, host TEXT, title TEXT NOT NULL, context TEXT NOT NULL,"
        " questions TEXT NOT NULL, blocking INTEGER NOT NULL, status TEXT NOT NULL, answer TEXT, answered_at REAL);"
        "INSERT INTO questions VALUES ('old1', 1, 'p', 's', NULL, NULL, 't', '', '[]', 1, 'waiting', NULL, NULL);"
    )
    db.close()
    s = Store(tmp_path / "aqua.db")
    assert s.get("old1")["set_aside_at"] is None
    assert s.set_aside("old1", True)["set_aside_at"]


def test_fonts_and_viewer_are_served(client):
    css = client.get("/fonts.css").text
    names = re.findall(r"url\((fonts/[^)]+\.woff2)\)", css)
    assert len(names) >= 3
    for name in names:
        r = client.get("/" + name)
        assert r.status_code == 200 and r.headers["content-type"] == "font/woff2" and r.content[:4] == b"wOF2"
    assert len(re.findall(r"--font-(sans|serif|mono):", css)) == 3
    assert client.get("/vendor/photoswipe.esm.min.js").status_code == 200


def test_changes_reports_the_page_build(client):
    """The page reloads itself when a release changes its files: the poll carries their fingerprint."""
    b = client.get("/api/changes?since=-1&wait=0").json()["build"]
    assert len(b) == 12 and b == client.get("/api/changes?since=-1&wait=0").json()["build"]


def test_page_files_are_revalidated(client):
    """Without this, Safari kept an installed iPad app on an old page for hours after a release."""
    for path in ("/", "/shell.js", "/markdown.css", "/api/changes?since=-1&wait=0"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


def test_token_guards_api_and_media_not_the_page(tmp_path):
    with TestClient(create_app(tmp_path, token="s3cret"), base_url="http://127.0.0.1") as c:
        assert c.get("/").status_code == 200 and c.get("/shell.js").status_code == 200
        assert c.get("/api/questions").status_code == 401
        assert c.get("/media/x.png").status_code == 401
        assert c.get("/api/questions", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/questions", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.post("/api/login", json={"token": "wrong"}).status_code == 401
        r = c.post("/api/login", json={"token": "s3cret"})
        assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
        assert c.get("/api/questions").status_code == 200  # the cookie from /api/login


def test_no_token_means_no_login(tmp_path, monkeypatch):
    monkeypatch.delenv("AQUA_TOKEN", raising=False)
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as c:
        assert c.get("/api/questions").status_code == 200
        assert c.post("/api/login", json={"token": "anything"}).status_code == 200


def test_unknown_host_is_refused(tmp_path, monkeypatch):
    """A page cannot reach a local server through a domain it points at 127.0.0.1 (DNS rebinding)."""
    monkeypatch.delenv("AQUA_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("AQUA_TOKEN", raising=False)
    with TestClient(create_app(tmp_path), base_url="http://127.0.0.1") as c:
        assert c.get("/api/questions").status_code == 200
        assert c.get("/api/questions", headers={"host": "localhost:18451"}).status_code == 200
        assert c.get("/api/questions", headers={"host": "[::1]:18451"}).status_code == 200
        assert c.get("/api/questions", headers={"host": "attacker.example"}).status_code == 400
        assert c.post("/api/questions", json=ASK, headers={"host": "attacker.example"}).status_code == 400
    with TestClient(create_app(tmp_path, allowed_hosts=["inbox.example:8460"]), base_url="http://inbox.example") as c:
        assert c.get("/api/questions").status_code == 200
        assert c.get("/", headers={"host": "127.0.0.1"}).status_code == 400
    with TestClient(create_app(tmp_path, token="t"), base_url="http://anything.example") as c:
        assert c.get("/").status_code == 200  # with a token, any host name; the token guards the data


def test_media_contents_must_match_the_claimed_type(client):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
    r = client.post("/api/media", files={"file": ("x.png", svg, "image/png")})
    assert r.status_code == 415
    assert client.get("/").headers["referrer-policy"] == "no-referrer"
