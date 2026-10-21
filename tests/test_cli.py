import json
import httpx
import pytest

from aqua import cli
from conftest import answer_when_posted


def test_blocking_ask_prints_the_answer(server, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("AQUA_USER_NAME", raising=False)
    ctx = tmp_path / "ctx.md"
    ctx.write_text("Long context with a [link](https://example.org).", encoding="utf-8")
    answer_when_posted(server, ["Yes"], note="go ahead")
    code = cli.main(["ask", "--title", "Run the pilot?", "--context-file", str(ctx), "--question", "Run it?",
                     "--option", "Yes::run now", "--option", "No"])
    out = capsys.readouterr().out
    assert code == 0
    assert "-> Yes" in out and "go ahead" in out and "The user answered question" in out
    q = httpx.get(server + "/api/questions?status=answered").json()["items"][0]
    assert q["session_name"] == "Test session" and q["title"] == "Run the pilot?"
    assert q["questions"][0]["options"][0] == {"label": "Yes", "description": "run now"}


def test_non_blocking_then_answer(server, capsys):
    assert cli.main(["ask", "--title", "T", "--question", "Q?", "--no-wait", "--json"]) == 0
    qid = json.loads(capsys.readouterr().out)["id"]
    assert cli.main(["answer", qid]) == cli.EXIT_WAITING
    assert "still waiting" in capsys.readouterr().out
    httpx.post(f"{server}/api/questions/{qid}/answer", json={"answers": ["typed answer"]})
    assert cli.main(["answer", qid]) == 0
    assert "-> typed answer" in capsys.readouterr().out


def test_questions_file_and_timeout(server, tmp_path, capsys):
    qf = tmp_path / "q.json"
    qf.write_text(json.dumps([{"header": "A", "question": "First?", "options": ["One::d1", {"label": "Two"}]},
                              {"question": "Second?", "multiSelect": True, "options": ["x", "y"]}]), encoding="utf-8")
    assert cli.main(["ask", "--title", "Two things", "--questions-file", str(qf), "--timeout", "1"]) == cli.EXIT_WAITING
    q = httpx.get(server + "/api/questions").json()["items"][0]
    assert [x["question"] for x in q["questions"]] == ["First?", "Second?"] and q["questions"][1]["multiSelect"]


def test_local_images_are_uploaded(server, tmp_path):
    (tmp_path / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n0000")
    out = cli.attach_local_media("See ![plot](shot.png) and [web](https://example.org)", tmp_path)
    assert "](/media/" in out and "(https://example.org)" in out


def test_repeated_flags_make_separate_questions(server, capsys):
    assert cli.main(["ask", "--title", "Two", "--no-wait", "--header", "A", "--question", "First?", "--option", "x::dx",
                     "--option", "y", "--question", "Second?", "--header", "B", "--multi", "--option", "z"]) == 0
    q = httpx.get(server + "/api/questions").json()["items"][0]
    assert q["questions"] == [
        {"header": "A", "question": "First?", "multiSelect": False,
         "options": [{"label": "x", "description": "dx"}, {"label": "y", "description": ""}]},
        {"header": "B", "question": "Second?", "multiSelect": True, "options": [{"label": "z", "description": ""}]}]


def test_option_without_question_is_refused(server):
    with pytest.raises(SystemExit):
        cli.main(["ask", "--title", "T", "--no-wait", "--option", "x"])


def test_user_name_setting(server, capsys, monkeypatch):
    monkeypatch.setenv("AQUA_USER_NAME", "ada")
    assert cli.main(["ask", "--title", "T", "--question", "Q?", "--no-wait"]) == 0
    assert "Asked ada: question" in capsys.readouterr().out
    assert cli.user_name(cap=True) == "Ada"


def test_config_file_and_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text('{"url": "http://example.test:1/", "user_name": "Ada"}', encoding="utf-8")
    monkeypatch.setenv("AQUA_CONFIG", str(cfg))
    monkeypatch.delenv("AQUA_URL", raising=False)
    monkeypatch.delenv("AQUA_USER_NAME", raising=False)
    assert cli.base_url() == "http://example.test:1" and cli.user_name() == "Ada"
    monkeypatch.setenv("AQUA_USER_NAME", "Bo")
    assert cli.user_name() == "Bo"
    monkeypatch.setenv("AQUA_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.delenv("AQUA_USER_NAME")
    assert cli.base_url() == cli.DEFAULT_URL and cli.user_name() == "the user"


def test_cli_sends_the_token(tmp_path, monkeypatch, capsys):
    import socket, threading, time
    import uvicorn
    from aqua.server import create_app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(tmp_path, token="tok"), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    time.sleep(0.5)
    monkeypatch.setenv("AQUA_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("AQUA_CONFIG", str(tmp_path / "none.json"))
    monkeypatch.delenv("AQUA_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="401"):
        cli.main(["ask", "--title", "T", "--question", "Q?", "--no-wait"])
    monkeypatch.setenv("AQUA_TOKEN", "tok")
    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    ctx = tmp_path / "c.md"
    ctx.write_text("![p](p.png)", encoding="utf-8")
    assert cli.main(["ask", "--title", "T", "--context-file", str(ctx), "--question", "Q?", "--no-wait"]) == 0
    srv.should_exit = True


def test_only_supported_media_is_uploaded(server, tmp_path, capsys):
    """A link to a text or SVG file stays a plain link instead of failing the whole ask with 415."""
    (tmp_path / "run.log").write_text("log", encoding="utf-8")
    (tmp_path / "d.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "p.png").write_bytes(bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 20)
    ctx = tmp_path / "c.md"
    ctx.write_text("[log](run.log) ![d](d.svg) ![p](p.png)", encoding="utf-8")
    assert cli.main(["ask", "--title", "T", "--context-file", str(ctx), "--question", "Q?", "--no-wait"]) == 0
    q = httpx.get(server + "/api/questions").json()["items"][0]
    assert "(run.log)" in q["context"] and "(d.svg)" in q["context"] and "(/media/" in q["context"]
