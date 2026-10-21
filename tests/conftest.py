import socket
import threading
import time

import httpx
import pytest
import uvicorn

from aqua.server import create_app


@pytest.fixture
def server(tmp_path, monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(create_app(tmp_path), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(url + "/api/questions")
            break
        except httpx.ConnectError:
            time.sleep(0.05)
    monkeypatch.setenv("AQUA_URL", url)
    monkeypatch.setenv("AQUA_CONFIG", str(tmp_path / "no-config.json"))  # never the real ~/.config/aqua
    monkeypatch.setenv("AQUA_SESSION_NAME", "Test session")
    yield url
    srv.should_exit = True
    t.join(5)


def answer_when_posted(url, answers, note=""):
    def run():
        for _ in range(200):
            items = httpx.get(url + "/api/questions").json()["items"]
            if items:
                httpx.post(f"{url}/api/questions/{items[0]['id']}/answer", json={"answers": answers, "note": note})
                return
            time.sleep(0.05)
    threading.Thread(target=run, daemon=True).start()
