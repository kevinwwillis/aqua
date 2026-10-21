"""aqua CLI for agents: ask the user a question with full context, then wait for (or later fetch) the answer.

Standard library only, so it runs anywhere Python 3.10+ runs.
"""

from __future__ import annotations

import argparse
import glob
import json
import mimetypes
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:18451"  # aqua-server's default address
# The media types the server accepts (server.MEDIA_TYPES).
UPLOAD_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "video/mp4", "video/webm", "application/pdf"}
EXIT_WAITING = 3  # the question is still waiting (timeout or --no-wait check)


def setting(name: str) -> str:
    """AQUA_<NAME> from the environment, else <name> from the config file, else ""."""
    value = os.environ.get(f"AQUA_{name.upper()}", "").strip()
    if value:
        return value
    path = Path(os.environ.get("AQUA_CONFIG") or Path.home() / ".config" / "aqua" / "config.json")
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get(name.lower(), "")).strip()
    except (OSError, ValueError, AttributeError):
        return ""


def user_name(cap: bool = False) -> str:
    """Who answers, as agents should call them: the user_name setting, or "the user"."""
    name = setting("user_name") or "the user"
    return name[:1].upper() + name[1:] if cap else name


def auth_headers() -> dict:
    token = setting("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def base_url() -> str:
    return (setting("url") or DEFAULT_URL).rstrip("/")


def request(method: str, path: str, body: dict | None = None, timeout: float = 70) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method,
                                 headers={**auth_headers(), **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"aqua: server said {e.code}: {detail}") from None


def upload(path: Path) -> str:
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(base_url() + "/api/media", data=body, method="POST",
                                 headers={**auth_headers(), "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["url"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"aqua: upload of {path} failed: {e.code} {e.read().decode(errors='replace')}") from None


LOCAL_LINK = re.compile(r"(!?\[[^\]]*\]\()(<[^>]+>|[^)\s]+)(\s+\"[^\"]*\")?\)")


def attach_local_media(markdown: str, base: Path) -> str:
    """Upload local files referenced as ![..](path) or [..](path) and point the links at the server copy."""
    def repl(m: re.Match) -> str:
        target = m.group(2).strip("<>")
        if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I) or target.startswith(("#", "mailto:", "/media/")):
            return m.group(0)
        p = Path(target)
        p = p if p.is_absolute() else base / p
        if not p.is_file() or mimetypes.guess_type(p.name)[0] not in UPLOAD_TYPES:
            return m.group(0)  # other files (text, SVG, ...) stay plain links; the server would refuse them
        return f"{m.group(1)}{upload(p)}{m.group(3) or ''})"
    return LOCAL_LINK.sub(repl, markdown)


def desktop_session_title() -> str | None:
    """The Claude desktop app's title for this session, when the agent runs inside the desktop app."""
    sid = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    appdata = os.environ.get("APPDATA")
    if not sid or not appdata:
        return None
    for f in glob.glob(os.path.join(appdata, "Claude", "claude-code-sessions", "*", "*", f"{sid}.json")):
        try:
            return json.loads(Path(f).read_text(encoding="utf-8")).get("title") or None
        except (OSError, ValueError):
            continue
    return None


def session_name(arg: str | None) -> str:
    return (arg or os.environ.get("AQUA_SESSION_NAME") or desktop_session_title()
            or (f"session {os.environ['CLAUDE_CODE_SESSION_ID'][:8]}" if os.environ.get("CLAUDE_CODE_SESSION_ID")
                else f"{socket.gethostname()} pid {os.getppid()}"))


def parse_option(text: str) -> dict:
    label, _, desc = text.partition("::")
    return {"label": label.strip(), "description": desc.strip()}


class QuestionPart(argparse.Action):
    """--header/--question/--option/--multi in command-line order; a repeated --header or --question starts a new question."""

    def __call__(self, parser, ns, value, option_string=None):
        qs = getattr(ns, "qparts", None) or []
        cur = qs[-1] if qs else None
        new = (cur is None or (self.dest == "question" and cur["question"])
               or (self.dest == "header" and (cur["header"] or cur["options"])))
        if new:
            cur = {"header": "", "question": "", "options": [], "multiSelect": False}
            qs.append(cur)
        if self.dest == "option":
            cur["options"].append(parse_option(value))
        elif self.dest == "multi":
            cur["multiSelect"] = True
        else:
            cur[self.dest] = value
        ns.qparts = qs


def build_questions(a: argparse.Namespace) -> list[dict]:
    if a.questions_file:
        raw = sys.stdin.read() if a.questions_file == "-" else Path(a.questions_file).read_text(encoding="utf-8")
        data = json.loads(raw)
        qs = data["questions"] if isinstance(data, dict) else data
        return [{"header": q.get("header", ""), "question": q["question"], "options": [
            o if isinstance(o, dict) else parse_option(o) for o in q.get("options", [])],
            "multiSelect": bool(q.get("multiSelect"))} for q in qs]
    qs = getattr(a, "qparts", None) or []
    if not qs:
        raise SystemExit("aqua: give --question (with --option ...) or --questions-file")
    if any(not q["question"] for q in qs):
        raise SystemExit("aqua: every --header, --option or --multi needs its --question")
    return qs


def read_context(a: argparse.Namespace) -> tuple[str, Path]:
    if a.context_file == "-":
        return sys.stdin.read(), Path.cwd()
    if a.context_file:
        p = Path(a.context_file)
        return p.read_text(encoding="utf-8"), p.resolve().parent
    return a.context or "", Path.cwd()


def format_answer(q: dict) -> str:
    if q["status"] == "withdrawn":
        return f"Question {q['id']} was withdrawn; {user_name()} did not answer it."
    if q["status"] == "waiting":
        return f"Question {q['id']} is still waiting for {user_name()}."
    a = q["answer"] or {}
    when = datetime.fromtimestamp(q["answered_at"]).strftime("%Y-%m-%d %H:%M")
    if a.get("cancelled"):
        lines = [f"{user_name(True)} dismissed question {q['id']} ({when}) without answering."]
    else:
        lines = [f"{user_name(True)} answered question {q['id']} ({when}):"]
        for item, ans in zip(q["questions"], a.get("answers", [])):
            label = f"[{item['header']}] " if item.get("header") else ""
            lines.append(f"\n{label}{item['question']}")
            if ans is None:
                lines.append("-> (skipped)")
            elif isinstance(ans, list):
                lines.extend(f"-> {x}" for x in ans)
            else:
                lines.append(f"-> {ans}")
    if a.get("note"):
        lines.append(f"\nNote from {user_name()}:\n{a['note']}")
    return "\n".join(lines)


def wait_for(qid: str, timeout: float | None) -> dict:
    start = time.monotonic()
    while True:
        left = None if timeout is None else timeout - (time.monotonic() - start)
        if left is not None and left <= 0:
            return request("GET", f"/api/questions/{qid}")
        step = 50 if left is None else max(1, min(50, left))
        try:
            q = request("GET", f"/api/questions/{qid}/wait?wait={step:.0f}", timeout=step + 20)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"aqua: connection problem ({e}); retrying in 10 s", file=sys.stderr)
            time.sleep(10)
            continue
        if q["status"] != "waiting":
            return q


def emit(q: dict, as_json: bool) -> int:
    print(json.dumps(q, indent=1) if as_json else format_answer(q))
    return EXIT_WAITING if q["status"] == "waiting" else 0


def cmd_ask(a: argparse.Namespace) -> int:
    context, base = read_context(a)
    body = {
        "title": a.title,
        "project": a.project or os.getcwd(),
        "session_name": session_name(a.session_name),
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "host": socket.gethostname(),
        "context": attach_local_media(context, base),
        "questions": build_questions(a),
        "blocking": not a.no_wait,
    }
    q = request("POST", "/api/questions", body)
    if a.no_wait:
        print(json.dumps({"id": q["id"]}) if a.json else
              f"Asked {user_name()}: question {q['id']}. Get the answer later with: aqua answer {q['id']}")
        return 0
    print(f"aqua: asked question {q['id']}; waiting for {user_name()}'s answer", file=sys.stderr)
    return emit(wait_for(q["id"], a.timeout), a.json)


def cmd_answer(a: argparse.Namespace) -> int:
    q = wait_for(a.id, a.timeout) if a.wait else request("GET", f"/api/questions/{a.id}")
    return emit(q, a.json)


def cmd_withdraw(a: argparse.Namespace) -> int:
    q = request("DELETE", f"/api/questions/{a.id}")
    print(f"Withdrew question {q['id']}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aqua", description="Ask the user a question through the aqua inbox.")
    sub = p.add_subparsers(dest="cmd", required=True)

    ask = sub.add_parser("ask", help="post a question; waits for the answer unless --no-wait")
    ask.add_argument("--title", required=True, help="a few words saying what the questions are about")
    ask.add_argument("--context-file", help="markdown file the user reads before answering ('-' for stdin)")
    ask.add_argument("--context", help="context markdown inline (prefer --context-file for anything long)")
    ask.add_argument("--questions-file", help="JSON: a list of {header, question, options, multiSelect} ('-' for stdin)")
    ask.add_argument("--question", action=QuestionPart, help="a question; repeat --header/--question/--option for more")
    ask.add_argument("--header", action=QuestionPart, help="short label for the question it is next to")
    ask.add_argument("--option", action=QuestionPart, help="'Label::description' (repeat for each option)")
    ask.add_argument("--multi", action=QuestionPart, nargs=0, help="that question allows several options")
    ask.add_argument("--project", help="project folder (default: current directory)")
    ask.add_argument("--session-name", help="this agent session's name (default: the desktop app's title)")
    ask.add_argument("--no-wait", action="store_true", help="non-blocking: print the id and return at once")
    ask.add_argument("--timeout", type=float, help="stop waiting after this many seconds (exit 3)")
    ask.add_argument("--json", action="store_true", help="print the full JSON record")
    ask.set_defaults(fn=cmd_ask)

    ans = sub.add_parser("answer", help="print the answer to a question (exit 3 if still waiting)")
    ans.add_argument("id")
    ans.add_argument("--wait", action="store_true", help="wait until the user answers")
    ans.add_argument("--timeout", type=float, help="with --wait: stop after this many seconds (exit 3)")
    ans.add_argument("--json", action="store_true")
    ans.set_defaults(fn=cmd_answer)

    wd = sub.add_parser("withdraw", help="take back a question that no longer needs an answer")
    wd.add_argument("id")
    wd.set_defaults(fn=cmd_withdraw)

    a = p.parse_args(argv)
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    return a.fn(a)
