import os
import sys

import anyio
import httpx
import pytest

pytest.importorskip("mcp")
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from conftest import answer_when_posted  # noqa: E402


async def call(url, tool, args, user=None):
    env = {k: v for k, v in os.environ.items() if k != "AQUA_USER_NAME"}
    env.update({"AQUA_URL": url, "AQUA_SESSION_NAME": "MCP test", **({"AQUA_USER_NAME": user} if user else {})})
    params = StdioServerParameters(command=sys.executable, args=["-c", "from aqua.mcp_server import main; main()"], env=env)
    async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
        await s.initialize()
        tools = {t.name for t in (await s.list_tools()).tools}
        assert {"ask_user", "wait_for_answer", "check_answer", "withdraw_question"} <= tools
        assert (f"ask_{user.lower()}" in tools) if user else not any(n.startswith("ask_") and n != "ask_user" for n in tools)
        res = await s.call_tool(tool, args)
        return res, "".join(c.text for c in res.content if getattr(c, "text", None))


def test_mcp_blocking_ask_returns_the_answer(server):
    answer_when_posted(server, [["a", "b"]], note="from the page")
    res, text = anyio.run(call, server, "ask_user", {
        "title": "MCP round trip", "context": "**ctx**",
        "questions": [{"question": "Pick?", "options": [{"label": "a"}, {"label": "b"}], "multiSelect": True}]})
    assert not res.is_error, text
    assert "-> a" in text and "-> b" in text and "from the page" in text
    q = httpx.get(server + "/api/questions?status=answered").json()["items"][0]
    assert q["title"] == "MCP round trip" and q["session_name"] == "MCP test"


def test_mcp_non_blocking_and_errors(server):
    res, text = anyio.run(call, server, "ask_user", {"title": "T", "questions": [{"question": "Q?"}], "blocking": False})
    assert "Asked the user: question" in text
    res, text = anyio.run(call, server, "check_answer", {"question_id": "nope"})
    assert res.is_error and "404" in text


def test_mcp_user_name_alias(server):
    """AQUA_USER_NAME=Ada also offers ask_ada, so agents that call it by name keep working, and texts use the name."""
    res, text = anyio.run(call, server, "ask_ada", {"title": "T", "questions": [{"question": "Q?"}], "blocking": False},
                          "Ada")
    assert not res.is_error and "Asked Ada: question" in text
