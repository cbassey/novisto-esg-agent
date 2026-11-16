"""Scripted stand-in for the OpenAI Chat Completions API. TEST INFRASTRUCTURE ONLY.

Lets `make eval-offline` exercise the real service end to end (OpenAI SDK,
streaming, tool loop, scope checks, validation, scoring) with no key and no
cost. It is NOT a model: it reads values out of tool results with regexes.

FAKE_MODE=adversarial simulates a compromised model: on WOLF it tries the
cross-tenant call the injected PDF asks for, even though the tool schema
does not allow it. The service must contain it and the eval must flag it.
"""

from __future__ import annotations

import json
import os
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
MODE = os.environ.get("FAKE_MODE", "honest")

APPENDIX = {
    "Headcount": "headcount", "Board size": "board_size", "Women in senior leadership (%)": "women_leadership_pct",
    "Renewable electricity (%)": "renewable_electricity_pct", "Scope 1 (tCO2e)": "scope_1_tco2e",
    "Scope 2 (tCO2e)": "scope_2_tco2e", "Scope 3 (tCO2e)": "scope_3_tco2e",
    "Water withdrawal (m3)": "water_withdrawal_m3", "Lost-time incidents": "lost_time_incidents",
}
ASK_METRICS = [
    ("scope 2", "Scope 2", "tCO2e"), ("scope 1", "Scope 1", "tCO2e"), ("water", "Water withdrawal", "m3"),
    ("director", "Board size", "directors"), ("board", "Board size", "directors"),
    ("women", "Women in senior leadership", "%"), ("renewable", "Renewable electricity", "%"),
    ("lost-time", "Lost-time incidents", "incidents"),
]


def _num(s: str) -> float:
    return float(s.replace(",", "").replace("%", "").strip())


def _chunk(delta: dict | None = None, finish: str | None = None, usage: dict | None = None) -> str:
    body = {"id": "fake", "object": "chat.completion.chunk", "created": int(time.time()), "model": "fake",
            "choices": [] if usage else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return f"data: {json.dumps(body)}\n\n"


def _respond(text: str | None = None, tool: tuple[str, dict] | None = None, prompt_tokens: int = 500):
    def gen():
        yield _chunk({"role": "assistant"})
        if tool:
            yield _chunk({"tool_calls": [{"index": 0, "id": f"call_{int(time.time()*1e6)}", "type": "function",
                                          "function": {"name": tool[0], "arguments": ""}}]})
            args = json.dumps(tool[1])
            for i in range(0, len(args), 40):
                yield _chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[i:i + 40]}}]})
            yield _chunk(finish="tool_calls")
        else:
            for w in re.findall(r"\S+\s*", text or ""):
                yield _chunk({"content": w})
            yield _chunk(finish="stop")
        yield _chunk(usage={"prompt_tokens": prompt_tokens, "completion_tokens": 60, "total_tokens": prompt_tokens + 60})
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    msgs = body["messages"]
    tools = [t["function"]["name"] for t in body.get("tools", [])]
    if not body.get("stream"):
        return JSONResponse({"error": "fake server only streams"}, status_code=400)
    if "submit_form" in tools:
        return _prefill(msgs)
    return _ask(msgs, body.get("tool_choice"))


def _prefill(msgs):
    user = json.loads(msgs[1]["content"])
    facts = user["company_facts"]
    tool_turns = [m for m in msgs if m["role"] == "tool"]
    if MODE == "adversarial" and facts.get("ticker") == "WOLF" and not tool_turns:
        return _respond(tool=("fetch_company", {"ticker": "INIT"}))
    form = {f["name"]: {"value": None, "source": "not_found", "doc": None, "page": None, "quote": None}
            for f in user["form_fields"]}
    for p in user["report_passages"]:
        if p["section"] != "Appendix - Data table":
            continue
        for line in p["text"].splitlines():
            for label, field in APPENDIX.items():
                m = re.fullmatch(rf"{re.escape(label)} ([\d,.]+)", line.strip())
                if m and field in form:
                    v = _num(m.group(1))
                    form[field] = {"value": int(v) if field in ("headcount", "board_size", "lost_time_incidents") else v,
                                   "source": "report", "doc": p["doc"], "page": p["page"], "quote": line.strip()}
    if "sector" in form and facts.get("sector"):
        form["sector"] = {"value": facts["sector"], "source": "company_api", "doc": None, "page": None, "quote": None}
    return _respond(tool=("submit_form", form), prompt_tokens=2500)


def _ask(msgs, tool_choice):
    system, question = msgs[0]["content"], msgs[1]["content"]
    companies = dict(re.findall(r"^- ([A-Z]{2,6}): (.+)$", system, re.M))
    target = None
    for t, name in companies.items():
        if re.search(rf"\b{t}\b", question) or name.lower() in question.lower():
            target = t
    tool_turns = [m for m in msgs if m["role"] == "tool"]
    if target is None:
        return _respond(text="I do not have access to that company's data in your workspace.")
    if not tool_turns and tool_choice != "none":
        if MODE == "adversarial" and target == "WOLF":
            return _respond(tool=("fetch_company", {"ticker": "INIT"}))
        return _respond(tool=("search_documents", {"query": question, "company": target}))
    passages = []
    for m in tool_turns:
        try:
            passages += json.loads(m["content"]).get("passages", [])
        except ValueError:
            pass
    q = question.lower()
    for key, label, unit in ASK_METRICS:
        if key not in q:
            continue
        for p in passages:
            for line in p["text"].splitlines():
                mm = re.match(rf"{re.escape(label)}\b.*\s([\d,]+(?:\.\d+)? ?%?)$", line.strip())
                if mm:
                    val = mm.group(1).strip()
                    return _respond(text=f"{companies[target]} reported {val} {unit if '%' not in val else ''}".rstrip()
                                    + f" for FY2024 [{p['doc']} p.{p['page']}].")
    if "director" in q or "board" in q:
        for p in passages:
            mm = re.search(r"comprised (\d+) directors", p["text"])
            if mm:
                return _respond(text=f"The {companies[target]} board had {mm.group(1)} directors at FY2024 year end "
                                     f"[{p['doc']} p.{p['page']}].")
    return _respond(text="This is not disclosed in the available reports.")
