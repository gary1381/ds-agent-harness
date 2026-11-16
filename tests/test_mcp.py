"""MCP server: per-role tool lists, calls through the protocol (in-process and stdio), and the
agent-facing configuration files (.mcp.json, skills, AGENTS.md)."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest
import yaml

from dsharness import mcp_server
from dsharness.tools import ROLE_TOOLS, make_context
from dsharness.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


def private_ws(base: Workspace, root: Path) -> Workspace:
    root.mkdir(parents=True)
    (root / "lake").symlink_to(base.lake, target_is_directory=True)
    (root / "sources").symlink_to(base.sources, target_is_directory=True)
    (root / "custody").mkdir()
    return Workspace(root).ensure()


@pytest.fixture(scope="module")
def ws(small_ws, tmp_path_factory) -> Workspace:
    return private_ws(small_ws, tmp_path_factory.mktemp("mcp") / "ws")


def run(coro_fn, *args):
    return anyio.run(coro_fn, *args)


async def _list(server) -> dict:
    async with Client(server) as c:
        tools = (await c.list_tools()).tools
        return {"names": [t.name for t in tools], "tools": {t.name: t for t in tools}, "instructions": c.instructions}


@pytest.mark.parametrize("role, cutoff", [("analyst", "score"), ("data_scientist", "train"),
                                          ("data_engineer", "score")])
def test_server_lists_exactly_the_roles_tools(ws, role, cutoff):
    ctx = make_context(ws, cutoff, role, f"agent:{role}")
    try:
        listed = run(_list, mcp_server.build_server(ctx))
    finally:
        ctx.close()
    assert listed["names"] == ROLE_TOOLS[role]
    for name, tool in listed["tools"].items():
        assert tool.description == mcp_server.DESCRIPTIONS[name]
        assert tool.input_schema["type"] == "object"
        assert tool.annotations.read_only_hint == (name in mcp_server.READ_ONLY)
    assert ctx.build_id in listed["instructions"]


def test_descriptions_steer_agents_to_governed_paths(ws):
    ctx = make_context(ws, "score", "analyst", "agent:analyst")
    try:
        listed = run(_list, mcp_server.build_server(ctx))
    finally:
        ctx.close()
    d = {n: t.description for n, t in listed["tools"].items()}
    assert "Prefer this over run_sql" in d["query_metric"]
    assert "UNCERTIFIED" in d["run_sql"] and "submit_answer" in d["run_sql"]
    assert "metric_call" in d["submit_answer"]
    props = listed["tools"]["submit_answer"].input_schema["properties"]
    assert {"question", "answer_rows", "sql", "metric_call", "claims"} <= set(props)
    assert "governed" in listed["instructions"].lower() and "UNCERTIFIED" in listed["instructions"]


async def _analyst_session(server):
    async with Client(server) as c:
        q = await c.call_tool("query_metric", {"metric": "net_revenue_fen", "dimensions": ["region"],
                                               "months": ["2026-03", "2026-03"]})
        body = q.structured_content
        sub = await c.call_tool("submit_answer", {"question": "What was net revenue by region for March 2026?",
                                                  "answer_rows": body["rows"], "metric_call": body["metric_call"]})
        refused = await c.call_tool("run_sql", {"sql": "SELECT email FROM bronze.crm_customers"})
        hidden = await c.call_tool("train_candidate", {"features": ["plan"], "model": "logreg",
                                                       "train_as_of": ["2026-01-01"]})
        bad_args = await c.call_tool("query_metric", {"metric": 7})
        return q, sub, refused, hidden, bad_args


def test_calls_go_through_the_governed_tools(ws):
    ctx = make_context(ws, "score", "analyst", "agent:mcp-analyst")
    try:
        q, sub, refused, hidden, bad_args = run(_analyst_session, mcp_server.build_server(ctx))
    finally:
        ctx.close()
    assert not q.is_error and q.structured_content["tier"] == "governed"
    assert sub.structured_content["tier"] == "VERIFIED" and sub.structured_content["gate"]["verdict"] == "ALLOW"
    assert sub.structured_content["trust_label"] == "T0"                 # reconciled to the Finance close
    cert = sub.structured_content["certificate"]
    assert cert["trust_label"] == "T0" and cert["requested_by"] == "agent:mcp-analyst" and cert["ai_generated"]
    assert refused.structured_content["kind"] == "refused"           # a refusal is a result, not a crash
    assert hidden.is_error and "Unknown tool" in hidden.content[0].text   # other roles' tools do not exist here
    assert bad_args.is_error
    audit = [json.loads(x) for x in (ws.registry / "audit.jsonl").read_text().splitlines()]
    mine = [(a["tool"], a["error_kind"]) for a in audit if a["principal"] == "agent:mcp-analyst"]
    assert mine == [("query_metric", None), ("submit_answer", None), ("run_sql", "refused"),
                    ("train_candidate", "rejected_by_protocol"), ("query_metric", "rejected_by_protocol")]
    assert ctx.calls_used == 5                                        # protocol rejections count too


async def _stdio(ws_root: Path):
    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "dsharness.mcp_server", "--role", "data_engineer", "--workspace", str(ws_root),
        "--principal", "agent:de-stdio"])
    async with Client(params) as c:
        names = [t.name for t in (await c.list_tools()).tools]
        r = await c.call_tool("read_pipeline_file", {"path": "models/stg_invoices.sql"})
        return names, r


def test_stdio_entry_point(ws):
    names, r = run(_stdio, ws.root)
    assert names == ROLE_TOOLS["data_engineer"]
    assert not r.is_error and "amount_cents" in r.structured_content["content"]


def test_main_refuses_to_start_without_a_ready_build(tmp_path, capsys):
    assert mcp_server.main(["--role", "analyst", "--workspace", str(tmp_path)]) == 2
    assert "cannot start" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        mcp_server.main(["--role", "superuser", "--workspace", str(tmp_path)])


# --------------------------------------------------------------------------- agent-facing config
def _mcp_role_map(cfg: dict) -> dict[str, str]:
    roles = {}
    for name, s in cfg.items():
        args = s["args"]
        roles[name] = args[args.index("--role") + 1]
    return roles


def test_mcp_json_launches_one_server_per_role():
    cfg = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    assert set(cfg) == {"dsh-analyst", "dsh-ds", "dsh-de"}
    for name, s in cfg.items():
        assert s["command"] == ".venv/bin/python" and s["args"][:2] == ["-m", "dsharness.mcp_server"]
        args = s["args"]
        assert "--workspace" in args and "--principal" in args
    assert _mcp_role_map(cfg) == {"dsh-analyst": "analyst", "dsh-ds": "data_scientist", "dsh-de": "data_engineer"}


@pytest.mark.skipif(not (ROOT / ".cursor" / "mcp.json").exists(),
                    reason="Cursor cloud-agent config is not shipped in the public copy")
def test_cursor_mcp_json_matches_roles_for_cloud_agents():
    cfg = json.loads((ROOT / ".cursor" / "mcp.json").read_text())["mcpServers"]
    assert set(cfg) == {"dsh-analyst", "dsh-ds", "dsh-de"}
    for s in cfg.values():
        assert s["command"] == "${workspaceFolder}/.venv/bin/python"
        assert s["args"][:2] == ["-m", "dsharness.mcp_server"]
    assert _mcp_role_map(cfg) == _mcp_role_map(
        json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    )


@pytest.mark.parametrize("skill, role", [("analytics-answer", "analyst"), ("churn-modeling", "data_scientist"),
                                         ("pipeline-breakfix", "data_engineer")])
def test_skills_are_valid_and_reference_real_tools(skill, role):
    text = (ROOT / ".claude" / "skills" / skill / "SKILL.md").read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "missing YAML front matter"
    front = yaml.safe_load(m.group(1))
    assert front["name"] == skill and 20 < len(front["description"]) <= 1024
    mentioned = set(re.findall(r"`([a-z_]+)`", text)) & {t for ts in ROLE_TOOLS.values() for t in ts}
    assert mentioned and mentioned <= set(ROLE_TOOLS[role]), mentioned - set(ROLE_TOOLS[role])
    assert "checklist" in text.lower()


def test_agents_md_states_the_rules():
    text = (ROOT / "AGENTS.md").read_text()
    for needle in ("custody/", "pytest", "submit_answer", "propose_pipeline_change", "workspace"):
        assert needle in text, needle
