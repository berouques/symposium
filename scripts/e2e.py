#!/usr/bin/env python3
"""Test run of symposia on a LIVE LLM against a running server.

    ./sym e2e                       # all scenarios, default profile
    ./sym e2e --only protocol,forced_limit
    ./sym e2e --profile my-llm --timeout 1200
    ./sym e2e --fake                # self-check of this script on the fake LLM (no network)

What it does: logs in as administrator, takes an LLM profile, creates a symposium for
each scenario from built-in characters, drives it to the end (answers ask_human itself),
records the transcript, registries and metrics, rates protocol quality (state block,
repeats, language, voting, limits) and writes a report.

Result: data/e2e/<stamp>/report.md, report.json, <scenario>/*.json and the archive
data/e2e/<stamp>.zip — attach it whole when reporting an issue.
Uses only the standard library and httpx (already a dependency).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROLE_PREFERENCE = ["Pragmatist", "Skeptic", "Synthesizer"]
TERMINAL = ("finished", "paused_error", "paused_manual")


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class Api:
    def __init__(self, url: str, login: str, password: str):
        self.c = httpx.Client(base_url=url.rstrip("/"), timeout=60.0)
        r = self.c.post("/api/auth/login", json={"login": login, "password": password})
        if r.status_code != 200:
            raise SystemExit(f"login failed ({r.status_code}): {r.text[:200]}")

    def get(self, path, **kw):
        r = self.c.get(path, **kw)
        r.raise_for_status()
        return r.json()

    def post(self, path, body=None):
        r = self.c.post(path, json=body or {})
        if r.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def patch(self, path, body):
        r = self.c.patch(path, json=body)
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------- scenarios

def scenarios(boot: dict, settings: dict) -> list[dict]:
    return [
        {"key": "protocol", "title": "Discussion under protocol v2 (free form)",
         "topic": "Should a small development team rewrite a working system from scratch?",
         "criterion": "The participants state the conditions under which a rewrite is justified and the conditions under which it is not.",
         "report": "Conclusions / Disagreements / Open questions", "form": "free", "max_rounds": 4},
        {"key": "disputatio", "title": "Scholastic disputation (humanities)",
         "topic": "Can artificial intelligence be a bearer of knowledge, and not only of information?",
         "criterion": "The main objections are examined and a reasoned resolution with the necessary distinctions is given.",
         "report": "Quaestio / Videtur quod / Sed contra / Respondeo", "form": "disputatio",
         "vote_policy": "supermajority", "max_rounds": 4},
        {"key": "scientific", "title": "Scientific form + web search (evidence)", "needs": "web",
         "topic": "How does sleep deprivation (under 6 hours) affect the quality of a programmer's work? Rely on the sources you find.",
         "criterion": "At least two sources are cited and a cautious conclusion with its limitations is formulated.",
         "report": "Claims with references to evidence [E#]", "form": "scientific", "max_rounds": 4,
         "first_member_prompt": "You must use the web_explorer subagent to find sources.", "tools": ["web_explorer", "web_fetch", "ask_human"]},
        {"key": "coder", "title": "Verification by code in the workspace", "needs": "workspace",
         "topic": "What is faster for n=32: recursive or iterative Fibonacci? Verify by measuring.",
         "criterion": "Both methods are timed and the conclusion is backed by the result of running code.",
         "report": "Measurements and conclusion", "form": "scientific", "max_rounds": 4,
         "first_member_prompt": "You must use the coder subagent (python) to measure the time.", "tools": ["coder", "ask_human"]},
        {"key": "rag", "title": "Working with the RAG library (archivist)", "needs": "rag",
         "topic": "What do the attached materials say about the key topics? Rely on the fragments you find.",
         "criterion": "The key topics of the materials are listed with references to the found fragments.",
         "report": "List of topics with references [E#]", "form": "free", "max_rounds": 3,
         "first_member_prompt": "You must use the archivarius subagent.", "tools": ["archivarius", "ask_human"]},
        {"key": "forced_limit", "title": "Hard round limit with an unreachable criterion",
         "topic": "Is there a single correct way to organize the work of any team?",
         "criterion": "All participants fully and unconditionally agree on one universal way.",
         "report": "Outcome and unresolved objections", "form": "free", "max_rounds": 2},
    ]


def pick_roles(api: "Api") -> list[dict]:
    """Three characters: Ilmari, Moderator, Sofia (built-in); otherwise the first three."""
    chars = api.get("/api/characters")
    by = {c["name"]: c for c in chars}
    chosen = [by[n] for n in ("Ilmari", "Moderator", "Sofia") if n in by]
    for c in chars:
        if len(chosen) >= 3:
            break
        if c not in chosen:
            chosen.append(c)
    return chosen[:3]


def run_scenario(api: Api, sc: dict, roles: list[dict], profile: dict, bootstrap: dict, timeout: int,
                 outdir: Path) -> dict:
    members = [{"character_id": ch["id"], "user_prompt": sc.get("first_member_prompt", "") if i == 0 else ""}
               for i, ch in enumerate(roles)]
    body = {"title": f"e2e: {sc['key']}", "topic": sc["topic"], "completion_criterion": sc["criterion"],
            "report_format": sc["report"], "language": "English", "api_profile_id": profile["id"],
            "max_rounds": sc["max_rounds"], "form": sc.get("form", "free"),
            "vote_policy": sc.get("vote_policy", "unanimous"), "members": members,
            "rag_folder_ids": [bootstrap["rag_folders"][0]["id"]] if sc.get("needs") == "rag" else [],
            "start_now": False}
    t0 = time.time()
    sid = api.post("/api/symposia", body)["id"]
    if sc.get("tools"):                      # characters default to ask_human only: grant the tools this scenario needs
        first = api.get(f"/api/symposia/{sid}")["members"][0]
        api.patch(f"/api/symposia/{sid}/members/{first['id']}", {"tools": sc["tools"]})
    api.post(f"/api/symposia/{sid}/start")
    log(f"  symposium #{sid} created and started")
    answered: set[int] = set()
    status, last_round = "active", 0
    while time.time() - t0 < timeout:
        time.sleep(3)
        d = api.get(f"/api/symposia/{sid}")
        status = d["status"]
        if d["current_round"] != last_round:
            last_round = d["current_round"]
            log(f"  round {last_round}, status {status}")
        if status == "waiting_human":
            req = api.get(f"/api/symposia/{sid}/hitl/pending").get("request")
            if req and req["id"] not in answered:
                answered.add(req["id"])
                opt = (req.get("options") or [None])[0]
                api.post(f"/api/symposia/{sid}/hitl/{req['id']}/answer",
                         {"option": opt or "", "text": "" if opt else "Use your own judgement."})
                log(f"  ask_human: «{req['question'][:60]}» → answered")
        if status in ("finished", "paused_error", "paused_manual"):
            break
    timed_out = status not in TERMINAL
    if timed_out:
        api.post(f"/api/symposia/{sid}/pause")
    if status == "finished":          # wait for the final report
        for _ in range(40):
            if any(m["kind"] == "report" for m in api.get(f"/api/symposia/{sid}/transcript")["messages"]):
                break
            time.sleep(3)
    detail = api.get(f"/api/symposia/{sid}")
    tr = api.get(f"/api/symposia/{sid}/transcript")
    reg = api.get(f"/api/symposia/{sid}/registries")
    usage = api.get(f"/api/symposia/{sid}/usage")
    sdir = outdir / sc["key"]
    sdir.mkdir(parents=True, exist_ok=True)
    for name, data in (("detail", detail), ("transcript", tr), ("registries", reg), ("usage", usage)):
        (sdir / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (sdir / "transcript.md").write_text(render_md(detail, tr, reg), encoding="utf-8")
    return evaluate(sc, detail, tr, reg, usage, time.time() - t0, timed_out, sid)


def render_md(detail: dict, tr: dict, reg: dict) -> str:
    names = {m["id"]: m["name"] for m in detail["members"]}
    out = [f"# {detail['title']} (#{detail['id']})", f"Topic: {detail['topic']}", ""]
    for m in tr["messages"]:
        who = names.get(m["member_id"], "—")
        if m["kind"] == "member":
            st = (m["meta"] or {}).get("state", {})
            out.append(f"**{who}:** {m['content']}\n\n`state:` claims={st.get('claims')} reply_to={st.get('reply_to')} "
                       f"evidence={st.get('evidence')} readiness={st.get('readiness')} flags={(m['meta'] or {}).get('flags')}\n")
        elif m["kind"] in ("evidence",):
            out.append(f"> **{(m['meta'] or {}).get('ref')}** ({(m['meta'] or {}).get('agent')}): {m['content'][:600]}\n")
        else:
            out.append(f"*[{m['kind']}] {who}: {m['content'][:800]}*\n")
    out.append("\n## Registries\n" + json.dumps(reg, ensure_ascii=False, indent=1))
    return "\n".join(out)


def cyr_share(text: str) -> float:
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", text)
    return (sum(1 for ch in letters if "а" <= ch.lower() <= "я" or ch in "Ёё") / len(letters)) if letters else 1.0


def evaluate(sc, detail, tr, reg, usage, secs, timed_out, sid) -> dict:
    msgs = tr["messages"]
    speeches = [m for m in msgs if m["kind"] == "member"]
    checks: list[tuple[str, str, str]] = []   # (name, PASS|WARN|FAIL, details)

    def chk(name, ok, detail_, severity="FAIL"):
        checks.append((name, "PASS" if ok else severity, detail_))

    chk("symposium finished", detail["status"] == "finished" and not timed_out,
        f"status={detail['status']}, end_reason={detail.get('end_reason')}, error={detail.get('error_info')}")
    n = max(1, len(speeches))
    missing = sum(1 for m in speeches if (m["meta"] or {}).get("flags", {}).get("state_missing"))
    chk("<state> block present", missing / n <= 0.1, f"without block: {missing}/{len(speeches)}", "WARN")
    claimless = sum(1 for m in speeches if not ((m["meta"] or {}).get("state") or {}).get("claims"))
    chk("new claims stated", claimless / n <= 0.3, f"without claims: {claimless}/{len(speeches)}", "WARN")
    later = [m for m in speeches if m["round_id"] != (speeches[0]["round_id"] if speeches else None)]
    noreply = sum(1 for m in later if not ((m["meta"] or {}).get("state") or {}).get("reply_to"))
    chk("reply_to present from round 2", not later or noreply / len(later) <= 0.3,
        f"without reply_to: {noreply}/{len(later)}", "WARN")
    rep = sum(1 for m in speeches if (m["meta"] or {}).get("flags", {}).get("repetition"))
    chk("no repeats after requests", rep == 0, f"replies flagged as repeats: {rep}", "WARN")
    trunc = sum(1 for m in speeches if (m["meta"] or {}).get("flags", {}).get("truncated"))
    chk("no truncated replies", trunc == 0, f"truncated: {trunc}", "WARN")
    share = max((1 - cyr_share(m["content"]) for m in speeches), default=0.0)
    chk("reply language (English)", share <= 0.2, f"highest share of non-Latin letters in a reply: {share:.0%}", "WARN")
    opening = next((m for m in msgs if m["kind"] == "secretary"), None)
    if opening is None:
        chk("secretary opened the symposium", False, "no secretary message")
    else:
        fb = (opening["meta"] or {}).get("fallback")
        chk("secretary opened the symposium", not fb, "FALLBACK opening used (the model returned an empty reply)" if fb else f"{len(opening['content'])} chars, checklist items: {len(reg['checklist'])}", "WARN")
    done_rounds = [r for r in tr["rounds"] if r["status"] == "done"]
    empty_dig = [r["number"] for r in done_rounds if not (r["summary"] or "").strip()]
    chk("round digests produced", not empty_dig, f"completed rounds: {len(done_rounds)}; without digest: {empty_dig}")
    long_dig = [r["number"] for r in done_rounds if len(r["summary"] or "") > 700]
    chk("digests compact (≤ 700 chars)", not long_dig, f"over the threshold: {long_dig}", "WARN")
    recov = [m["content"] for m in msgs if m["kind"] == "system" and "AUTO-RECOVERY" in m["content"]]
    chk("no auto-recoveries needed", not recov, "; ".join(recov[:3]), "WARN")
    votes = [m for m in msgs if m["kind"] == "vote"]
    pos = [((m["meta"] or {}).get("position")) for m in votes]
    chk("completion procedure took place", bool(votes), f"ballots: {len(votes)} {pos}", "WARN")
    chk("final report received", any(m["kind"] == "report" and len(m["content"]) > 60 for m in msgs), "")
    if sc.get("needs") in ("web", "workspace", "rag"):
        agent = {"web": "web_explorer", "workspace": "coder", "rag": "archivarius"}[sc["needs"]]
        ev = [e for e in reg["evidence"] if e["agent"] == agent]
        okev = [e for e in ev if e["ok"]]
        chk(f"evidence from {agent}", bool(okev),
            f"calls: {len(ev)}, successful: {len(okev)}" + (f"; error: {ev[0]['digest'][:160]}" if ev and not okev else ""))
    if sc["key"] == "forced_limit":
        chk("round limit respected", detail["current_round"] <= sc["max_rounds"] + 1,
            f"current_round={detail['current_round']}, max_rounds={sc['max_rounds']}")
    tokens = (usage.get("tokens_in", 0) or 0) + (usage.get("tokens_out", 0) or 0)
    calls = usage.get("calls", 0)
    wasted = max(0, calls - len(speeches) - len(votes) - 1 - len(done_rounds))
    chk("few wasted LLM calls", wasted <= max(3, len(speeches)), f"calls: {calls}, useful ≈ {len(speeches) + len(votes) + 1}, wasted ≈ {wasted} (repeats, cut-offs, requests)", "WARN")
    verdict = "FAIL" if any(c[1] == "FAIL" for c in checks) else ("WARN" if any(c[1] == "WARN" for c in checks) else "PASS")
    return {"key": sc["key"], "title": sc["title"], "symposium_id": sid, "verdict": verdict, "seconds": round(secs),
            "rounds": detail["current_round"], "speeches": len(speeches), "tokens": tokens,
            "end_reason": detail.get("end_reason"), "checks": checks,
            "checklist": reg["checklist"], "issues": reg["issues"], "evidence": len(reg["evidence"]),
            "calls": calls, "by_type": [{"type": t["call_type"], "tokens": t["tokens_in"] + t["tokens_out"], "calls": t["calls"]} for t in usage.get("by_type", [])]}


def write_report(outdir: Path, results: list[dict], meta: dict) -> None:
    (outdir / "report.json").write_text(json.dumps({"meta": meta, "results": results}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    lines = [f"# Symposium e2e — {meta['stamp']}", "",
             f"Server: {meta['url']} · version {meta.get('version')} · profile: {meta['profile']}", "",
             "| scenario | verdict | rounds | replies | tokens | time | ended |", "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['key']} | **{r['verdict']}** | {r.get('rounds', '-')} | {r.get('speeches', '-')} | "
                     f"{r.get('tokens', '-')} | {r.get('seconds', '-')}s | {r.get('end_reason') or '-'} |")
    for r in results:
        lines += ["", f"## {r['title']} — {r['verdict']}"]
        if r.get("skipped"):
            lines.append(f"Skipped: {r['skipped']}")
            continue
        if r.get("crash"):
            lines.append(f"Script failure: {r['crash']}")
            continue
        for name, sev, det in r["checks"]:
            lines.append(f"- [{sev}] {name}" + (f" — {det}" if det else ""))
        if r.get("by_type"):
            lines.append("- usage by type: " + ", ".join(f"{t['type']} {t['tokens']} tok/{t['calls']} calls" for t in r["by_type"]))
        if r["checklist"]:
            lines.append("- checklist: " + ", ".join(f"{c['ref']}={c['status']}" for c in r["checklist"]))
        if r["issues"]:
            lines.append("- objections: " + "; ".join(f"{i['ref']}[{i['status']}] {i['text'][:80]}" for i in r["issues"]))
    (outdir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("SYMPOSIUM_URL", "http://127.0.0.1:23606"))
    ap.add_argument("--login", default="admin")
    ap.add_argument("--password", default=os.environ.get("SYMPOSIUM_ADMIN_PASSWORD", "admin"))
    ap.add_argument("--profile", default=None, help="name or id of the LLM profile (default: the first)")
    ap.add_argument("--only", default="", help="scenario keys, comma-separated")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per scenario")
    ap.add_argument("--out", default="data/e2e")
    ap.add_argument("--fake", action="store_true", help="self-check on the fake LLM")
    args = ap.parse_args()

    api = Api(args.url, args.login, args.password)
    bootstrap = api.get("/api/bootstrap")
    settings = {s["key"]: s["value"] for s in api.get("/api/admin/settings")} \
        if isinstance(api.get("/api/admin/settings"), list) else api.get("/api/admin/settings")
    if args.fake:
        pid = api.post("/api/admin/profiles", {"name": "e2e-fake", "base_url": "http://localhost",
                                               "model": "fake:basic", "api_key": "x"})["id"]
        profile = {"id": pid, "name": "e2e-fake", "model": "fake:basic"}
    else:
        profiles = api.get("/api/admin/profiles")
        if not profiles:
            raise SystemExit("no LLM profiles: set SYMPOSIUM_BOOT_* in .env or create a profile in the admin panel")
        profile = next((p for p in profiles if args.profile in (str(p["id"]), p["name"])), None) \
            if args.profile else profiles[0]
        if profile is None:
            raise SystemExit(f"profile '{args.profile}' not found")
    log(f"profile: #{profile['id']} {profile['name']} [{profile['model']}]")
    probe = api.post(f"/api/admin/profiles/{profile['id']}/test")
    log(f"connection probe: {'OK' if probe.get('ok') else 'ERROR'} — {probe.get('detail')}")
    if not probe.get("ok"):
        return 2

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = Path(args.out) / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    roles = pick_roles(api)
    wanted = {k.strip() for k in args.only.split(",") if k.strip()}
    ws_ok = True
    results: list[dict] = []
    for sc in scenarios(bootstrap, settings):
        if wanted and sc["key"] not in wanted:
            continue
        log(f"== {sc['key']}: {sc['title']}")
        skip = None
        if sc.get("needs") == "rag" and not bootstrap["rag_folders"]:
            skip = "no RAG folders (upload documents to the library)"
        if sc.get("needs") == "web" and not (settings.get("web_search_searxng_url") or "").strip() and not args.fake:
            skip = "web_search_searxng_url is not set"
        if skip:
            log(f"  skipped: {skip}")
            results.append({"key": sc["key"], "title": sc["title"], "verdict": "SKIP", "skipped": skip, "checks": []})
            continue
        try:
            res = run_scenario(api, sc, roles, profile, bootstrap, args.timeout, outdir)
        except Exception as e:  # noqa: BLE001
            log(f"  FAILURE: {e}")
            res = {"key": sc["key"], "title": sc["title"], "verdict": "FAIL", "crash": str(e), "checks": []}
        log(f"  verdict: {res['verdict']}")
        results.append(res)

    try:
        usage = api.get("/api/admin/profiles")
        prof_usage = next((p["limits_usage"] for p in usage if p["id"] == profile["id"]), None)
    except Exception:  # noqa: BLE001
        prof_usage = None
    meta = {"stamp": stamp, "url": args.url, "profile": profile["name"],
            "version": bootstrap.get("version"), "limits_usage": prof_usage}
    write_report(outdir, results, meta)
    archive = shutil.make_archive(str(outdir), "zip", str(outdir))
    log(f"report: {outdir / 'report.md'}")
    log(f"archive to send: {archive}")
    return 0 if all(r["verdict"] in ("PASS", "WARN", "SKIP") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
