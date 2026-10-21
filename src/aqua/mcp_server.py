"""aqua MCP server (stdio): the same ask / answer / withdraw as the CLI, as MCP tools for agents."""

from __future__ import annotations

import asyncio
import os
import re
import socket
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from . import cli

# A tool call waits at most this long; then it returns "still waiting" with the id, so client tool-call timeouts
# never lose a question. The agent calls wait_for_answer to keep waiting.
WAIT_SLICE_S = 9 * 60

USER = cli.user_name()

server = MCPServer(
    name="aqua",
    instructions=(
        f"Ask {USER} questions through aqua. Put everything they should read first (findings, numbers, links, local "
        f"image paths) in `context` as markdown, at full length: {USER} reads it in the aqua inbox, not in this chat. "
        "Give every question set a short `title`."
    ),
)


class Option(BaseModel):
    label: str = Field(description="The choice, a few words")
    description: str = Field(default="", description="What picking it means (markdown)")


class Question(BaseModel):
    question: str = Field(description="The question (markdown)")
    header: str = Field(default="", description="Short label, a word or two")
    options: list[Option] = Field(default_factory=list, description="Choices; the user can always type their own answer")
    multiSelect: bool = Field(default=False, description="The user may pick several options")


async def _call(fn, *args):
    """Run a CLI helper in a thread; its SystemExit (server error) becomes a tool error instead of ending the server."""
    try:
        return await asyncio.to_thread(fn, *args)
    except SystemExit as e:
        raise ToolError(str(e)) from None


async def _wait(qid: str, seconds: float) -> dict:
    return await _call(cli.wait_for, qid, seconds)


def _result(q: dict) -> str:
    if q["status"] == "waiting":
        return (f"Question {q['id']} is still waiting for {USER}. Call wait_for_answer with this id to keep waiting, "
                "or check_answer later.")
    return cli.format_answer(q)


async def ask_user(title: str, questions: list[Question], context: str = "", blocking: bool = True) -> str:
    body = {
        "title": title,
        "project": os.getcwd(),
        "session_name": cli.session_name(None),
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"),
        "host": socket.gethostname(),
        "context": await _call(cli.attach_local_media, context, Path.cwd()),
        "questions": [q.model_dump() for q in questions],
        "blocking": blocking,
    }
    q = await _call(cli.request, "POST", "/api/questions", body)
    if not blocking:
        return f"Asked {USER}: question {q['id']}. Get the answer later with check_answer."
    return _result(await _wait(q["id"], WAIT_SLICE_S))


ASK_DOC = f"""Ask {USER} one or more questions, with the context they need, through the aqua inbox.

title: a few words saying what the questions are about (shown in the inbox list).
context: markdown {USER} reads before answering; local image paths are uploaded and shown.
blocking: true waits for the answer (up to 9 minutes per call, then call wait_for_answer);
false returns the question id at once (get the answer later with check_answer).
"""
server.add_tool(ask_user, name="ask_user", description=ASK_DOC)
# With a user name set (AQUA_USER_NAME or the config file, for example "Ada"), the same tool is also offered as ask_<name> (ask_ada).
_alias = re.sub(r"[^a-z0-9]+", "_", cli.setting("user_name").lower()).strip("_")
if _alias and _alias != "user":
    server.add_tool(ask_user, name=f"ask_{_alias}", description=ASK_DOC)


@server.tool()
async def wait_for_answer(question_id: str) -> str:
    """Keep waiting (up to 9 minutes) for the answer to a question asked through aqua."""
    return _result(await _wait(question_id, WAIT_SLICE_S))


@server.tool()
async def check_answer(question_id: str) -> str:
    """Return the answer to a question asked through aqua, or say it is still waiting. Does not wait."""
    return _result(await _call(cli.request, "GET", f"/api/questions/{question_id}"))


@server.tool()
async def withdraw_question(question_id: str) -> str:
    """Take back a question you no longer need answered, so it leaves the aqua inbox."""
    q = await _call(cli.request, "DELETE", f"/api/questions/{question_id}")
    return f"Withdrew question {q['id']}."


def main() -> None:
    server.run("stdio")
