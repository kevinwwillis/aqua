"""aqua server: agents post questions, the user answers them in the web inbox, agents read the answer."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .store import Store

STATIC = Path(__file__).parent / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("font/woff2", ".woff2")  # the page's fonts


def static_build() -> str:
    """A fingerprint of the web page's files. The page compares it on every poll and reloads itself when a release
    changed it, because the iPad home-screen app otherwise keeps showing the old page until it is closed."""
    h = hashlib.sha256()
    for f in sorted(p for p in STATIC.rglob("*") if p.is_file()):
        h.update(f.relative_to(STATIC).as_posix().encode() + b"\0" + f.read_bytes())
    return h.hexdigest()[:12]
# No SVG: an SVG opened as a page runs its scripts on aqua's origin.
MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm", "application/pdf"}
MAX_MEDIA = 50 * 1024 * 1024


def looks_like(ctype: str, data: bytes) -> bool:
    """The file starts the way its claimed type does, so an SVG or HTML page cannot be stored as an image."""
    head = data[:16]
    return {
        "image/png": head.startswith(bytes([0x89]) + b"PNG"),
        "image/jpeg": head.startswith(bytes([0xFF, 0xD8, 0xFF])),
        "image/gif": head.startswith((b"GIF87a", b"GIF89a")),
        "image/webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
        "video/mp4": head[4:8] == b"ftyp",
        "video/webm": head.startswith(bytes([0x1A, 0x45, 0xDF, 0xA3])),
        "application/pdf": head.startswith(b"%PDF-"),
    }.get(ctype, False)


class Option(BaseModel):
    label: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20_000)


class Question(BaseModel):
    header: str = Field(default="", max_length=60)
    question: str = Field(min_length=1, max_length=20_000)
    options: list[Option] = Field(default_factory=list, max_length=12)
    multiSelect: bool = False


class Ask(BaseModel):
    title: str = Field(min_length=1, max_length=80, description="A few words saying what the questions are about")
    project: str = Field(min_length=1, max_length=500, description="The agent's project folder")
    session_name: str = Field(min_length=1, max_length=200, description="The agent session's name")
    session_id: str | None = Field(default=None, max_length=200)
    host: str | None = Field(default=None, max_length=200)
    context: str = Field(default="", max_length=1_000_000, description="Markdown the user reads before answering")
    questions: list[Question] = Field(min_length=1, max_length=8)
    blocking: bool = True

    @field_validator("title", "project", "session_name")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class Answer(BaseModel):
    # One entry per question, in order: a string, a list of strings (multi-select), or null (skipped).
    answers: list[str | list[str] | None] = Field(default_factory=list)
    note: str = Field(default="", max_length=100_000)
    cancelled: bool = False


class Aside(BaseModel):
    aside: bool


class Login(BaseModel):
    token: str = Field(max_length=1000)


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def request_host(header: str) -> str:
    """The host name of a Host header, without its port ('[::1]:8460' -> '::1')."""
    header = header.strip().lower()
    if header.startswith("["):
        return header[1:header.find("]")] if "]" in header else header
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def create_app(data_dir: Path | None = None, token: str | None = None,
               allowed_hosts: list[str] | None = None) -> FastAPI:
    """token (or AQUA_TOKEN): when set, the API and media need it; agents send it as a Bearer header, browsers get it
    as a cookie once per device by opening the page at /#token=<token>. Unset: no login, for a server only its user
    can reach.

    allowed_hosts (or AQUA_ALLOWED_HOSTS, comma-separated): the host names the server answers to; others get 400, so
    a web page cannot reach a local server by pointing its own domain at 127.0.0.1 (DNS rebinding). Default: the
    local names, or any name when a token is set. "*" allows any name."""
    data_dir = Path(data_dir or os.environ.get("AQUA_DATA", "data"))
    token = (os.environ.get("AQUA_TOKEN", "") if token is None else token).strip()
    if allowed_hosts is None:
        env_hosts = [h.strip().lower() for h in os.environ.get("AQUA_ALLOWED_HOSTS", "").split(",") if h.strip()]
        allowed_hosts = env_hosts or (["*"] if token else list(LOCAL_HOSTS))
    hosts = {request_host(h) if h != "*" else "*" for h in allowed_hosts}
    store = Store(data_dir / "aqua.db")
    media_dir = data_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    changed = asyncio.Event()  # replaced on every change; waiters wait on the current one
    state = {"event": changed, "version": 0}
    build = static_build()

    def bump() -> None:
        state["version"] += 1
        old = state["event"]
        state["event"] = asyncio.Event()
        old.set()

    app = FastAPI(title="aqua", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.post("/api/questions", status_code=201)
    async def ask(body: Ask) -> dict:
        q = store.create(body.model_dump())
        bump()
        return q

    @app.get("/api/questions")
    async def list_questions(status: str = Query("waiting")) -> dict:
        if status not in ("waiting", "answered", "withdrawn"):
            raise HTTPException(400, "status must be waiting, answered or withdrawn")
        # every waiting question is listed; history is capped
        return {"version": state["version"], "build": build, "items": store.list(status, limit=None if status == "waiting" else 200)}

    @app.get("/api/changes")
    async def changes(since: int = Query(0), wait: float = Query(25, ge=0, le=60)) -> dict:
        """Long-poll for the inbox: returns as soon as anything changed after `since`."""
        if state["version"] == since and wait:
            try:
                await asyncio.wait_for(state["event"].wait(), timeout=wait)
            except TimeoutError:
                pass
        return {"version": state["version"], "build": build}

    @app.get("/api/questions/{qid}")
    async def get_question(qid: str) -> dict:
        q = store.get(qid)
        if not q:
            raise HTTPException(404, "no such question")
        return q

    @app.post("/api/questions/{qid}/answer")
    async def answer(qid: str, body: Answer) -> dict:
        q = store.get(qid)
        if not q:
            raise HTTPException(404, "no such question")
        if not body.cancelled and len(body.answers) != len(q["questions"]):
            raise HTTPException(422, f"expected {len(q['questions'])} answers, got {len(body.answers)}")
        done = store.finish(qid, "answered", body.model_dump())
        if not done:
            raise HTTPException(409, f"question is already {store.get(qid)['status']}")
        bump()
        return done

    @app.post("/api/questions/{qid}/aside")
    async def set_aside(qid: str, body: Aside) -> dict:
        """The user's "Set aside" list: the question stays waiting for the agent, out of the main list."""
        q = store.get(qid)
        if not q:
            raise HTTPException(404, "no such question")
        done = store.set_aside(qid, body.aside)
        if not done:
            raise HTTPException(409, f"question is already {q['status']}")
        bump()
        return done

    @app.delete("/api/questions/{qid}")
    async def withdraw(qid: str) -> dict:
        """The agent takes its question back (it no longer needs the answer)."""
        q = store.get(qid)
        if not q:
            raise HTTPException(404, "no such question")
        done = store.finish(qid, "withdrawn", None)
        if not done:
            raise HTTPException(409, f"question is already {q['status']}")
        bump()
        return done

    @app.get("/api/questions/{qid}/wait")
    async def wait_answer(qid: str, wait: float = Query(50, ge=0, le=60)) -> dict:
        """Long-poll for the agent: returns when the question leaves 'waiting', or after `wait` seconds."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait
        while True:
            q = store.get(qid)
            if not q:
                raise HTTPException(404, "no such question")
            left = deadline - loop.time()
            if q["status"] != "waiting" or left <= 0:
                return q
            try:
                await asyncio.wait_for(state["event"].wait(), timeout=left)
            except TimeoutError:
                pass

    @app.post("/api/media", status_code=201)
    async def upload(file: UploadFile, request: Request) -> dict:
        ctype = file.content_type or mimetypes.guess_type(file.filename or "")[0] or ""
        if ctype not in MEDIA_TYPES:
            raise HTTPException(415, f"unsupported media type {ctype!r}")
        data = await file.read(MAX_MEDIA + 1)
        if len(data) > MAX_MEDIA:
            raise HTTPException(413, "file over 50 MB")
        if not looks_like(ctype, data):
            raise HTTPException(415, f"the file's contents are not {ctype}")
        ext = mimetypes.guess_extension(ctype) or ""
        name = hashlib.sha256(data).hexdigest()[:24] + ext
        (media_dir / name).write_bytes(data)
        return {"url": f"/media/{name}"}

    @app.get("/media/{name}")
    async def media(name: str) -> FileResponse:
        path = (media_dir / name).resolve()
        if path.parent != media_dir.resolve() or not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/api/login")
    async def login(body: Login, request: Request) -> JSONResponse:
        """The page trades the token (from its #token= address, which never reaches the server) for a cookie."""
        if token and not hmac.compare_digest(body.token.encode(), token.encode()):
            raise HTTPException(401, "wrong token")
        r = JSONResponse({"ok": True})
        if token:
            r.set_cookie("aqua_token", token, max_age=400 * 86400, httponly=True, samesite="strict",
                         secure=request.url.scheme == "https")
        return r

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        # The page's own files hold no questions, so they load without the token; the API and media need it.
        path = request.url.path
        if token and path != "/api/login" and (path.startswith("/api/") or path.startswith("/media/")):
            auth = request.headers.get("authorization", "")
            given = auth[7:] if auth.lower().startswith("bearer ") else request.cookies.get("aqua_token", "")
            if not hmac.compare_digest(given.encode(), token.encode()):
                return JSONResponse({"detail": "aqua token required"}, 401)
        return await call_next(request)

    @app.middleware("http")
    async def revalidate(request: Request, call_next):
        # Without a Cache-Control header Safari picks its own freshness from Last-Modified and kept the iPad on an
        # old page for hours after a release, through app restarts. no-cache: keep the file, but ask every time
        # (a 304 when nothing changed).
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        # Images and links in a question's context point at other sites; they need not learn this server's address.
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    @app.middleware("http")
    async def check_host(request: Request, call_next):
        # Added last, so it runs first.
        if "*" not in hosts and request_host(request.headers.get("host", "")) not in hosts:
            return JSONResponse({"detail": "unknown host; add it to AQUA_ALLOWED_HOSTS"}, 400)
        return await call_next(request)

    return app


def main() -> None:
    import uvicorn

    host = os.environ.get("AQUA_HOST", "127.0.0.1")
    port = int(os.environ.get("AQUA_PORT", "18451"))
    uvicorn.run(create_app(), host=host, port=port, log_level="info")
