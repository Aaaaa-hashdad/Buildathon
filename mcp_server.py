"""MCP server for the LinkedIn Data Bot.

Lets Claude (Claude Code / Claude Desktop, on your normal subscription, no API key) search the career
sites and open job feeds, read the results the app saved, read your master resume, and save tailored
resumes as Word files. LinkedIn runs only from the app: the one LinkedIn tool, request_linkedin_search,
just asks the open app to run a search; it never touches LinkedIn itself.

Run by Claude automatically (Connect AI in the app); you don't start it yourself.
Web mode for apps that need a URL (Superhuman Go): python mcp_server.py --web
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.shared import Pt
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from openpyxl import load_workbook

import job_boards
import linkedin_bot as bot

HERE = Path(__file__).parent
PROFILE_DIR = HERE / "profile"          # put your master resume here
RESUME_DIR = HERE / "resumes"           # tailored resumes are saved here

mcp = FastMCP("linkedin-bot")
READ_ONLY = ToolAnnotations(readOnlyHint=True)     # lets an AI app skip the approve prompt for reads


ACTIVITY_LOG = HERE / "mcp_activity.log"    # what the AI tools did and what went wrong; git-ignored


def _log(msg):
    print(msg, file=sys.stderr, flush=True)    # stdout belongs to the MCP protocol


def _note(msg):
    try:
        with ACTIVITY_LOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")
    except OSError:
        pass


def _logged(fn):
    """Records each tool call and any error in the activity log, then lets the error through."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        _note(f"{fn.__name__} {kw or a}")
        try:
            result = fn(*a, **kw)
        except Exception as e:
            _note(f"  ERROR {type(e).__name__}: {e}")
            raise
        if isinstance(result, dict) and result.get("ok") is False:
            _note(f"  FAILED {str(result)[:300]}")
        return result
    return wrapper


def _results_file(name):
    path = (bot.OUTPUT_DIR / name).resolve()
    if path.parent != bot.OUTPUT_DIR.resolve() or not path.exists():
        raise ValueError(f"No results file named {name!r}. Call list_results to see the files.")
    return path


def _rows(path):
    rows = list(load_workbook(path, read_only=True).active.iter_rows(values_only=True))
    header = [str(h) for h in rows[0]]
    return [dict(zip(header, (v if v is not None else "" for v in r))) for r in rows[1:]]


@mcp.tool(annotations=READ_ONLY)
def list_results() -> dict:
    """List the Excel files the bot has saved, newest first (kind, search, date, row count).

    If the app is running a LinkedIn search, this WAITS (up to about 50 seconds) for it to finish. When
    "search" is "running" the search is still going: call list_results again right away, don't ask the user.
    When "search" is "done" the newest file is the new results: read it.
    """
    import time
    waited = 0
    while _search_state()[0] == "running" and waited < 50:
        time.sleep(2)
        waited += 2
    state, st = _search_state()
    out = []
    for f in sorted(bot.OUTPUT_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True):
        kind = f.stem.split("_", 1)[0]
        out.append({"file": f.name, "kind": kind,
                    "saved": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "rows": len(_rows(f))})
    out_ = {"search": state, "just_finished": st.get("files") if state == "done" else None, "files": out}
    if state == "running":
        out_["next"] = ("THE SEARCH IS STILL RUNNING. Call list_results again RIGHT NOW. Do not write a message to the "
                        "user, do not report status, do not stop. Repeat until 'search' is 'done' (up to 40 calls).")
    elif state == "done":
        out_["next"] = "Search finished. Read just_finished with read_results (include_description=true) and continue."
        if st.get("problem"):
            out_["problem"] = st["problem"][:200]
            out_["next"] += " The batch stopped early because LinkedIn needs the user's attention; tell them."
    return out_


@mcp.tool(annotations=READ_ONLY)
def read_results(file: str, offset: int = 0, limit: int = 25, include_description: bool = False) -> dict:
    """Read rows from a results file. Each row has a 'row' number to use with get_job.

    Descriptions are left out unless include_description is true; then each is cut to 1500 characters, enough to
    judge a job. Read 8-10 rows per call this way instead of calling get_job on every row (get_job = full text).
    """
    rows = _rows(_results_file(file))
    page = []
    for i, r in enumerate(rows[offset:offset + limit], start=offset):
        r = dict(r, row=i)
        if "Description" in r:
            text = str(r["Description"])
            r["Description"] = text[:1500] if include_description else f"({len(text)} chars, use get_job)"
        page.append(r)
    return {"total": len(rows), "offset": offset, "rows": page}


@mcp.tool(annotations=READ_ONLY)
def get_job(file: str, row: int) -> dict:
    """Get one job with its full description (row number from read_results)."""
    rows = _rows(_results_file(file))
    if not 0 <= row < len(rows):
        raise ValueError(f"row must be 0-{len(rows) - 1}")
    return dict(rows[row], row=row)


@mcp.tool()
@_logged
def search_job_boards(query: str = "", location: str = "") -> dict:
    """Search the company career sites on the list (Greenhouse, Lever, Ashby, Workable,
    SmartRecruiters) and save matches to Excel. Uses public job feeds, not LinkedIn, so it's
    safe to run without asking.

    query: words that must all appear in the job title ("civil engineer"); blank = every job.
    location: one or more places, comma-separated ("Houston, Remote"); blank = anywhere.
    """
    logs = []
    path = job_boards.run(query, location, log=lambda m: (logs.append(m), _log(m)))
    if not path:
        return {"ok": False, "log": logs[-8:]}
    return {"ok": True, "file": Path(path).name, "rows": len(_rows(Path(path))), "log": logs[-8:]}


@mcp.tool()
@_logged
def public_search(query: str, location: str = "", days: int = 7, include_remote: bool = True, limit: int = 30,
                  web: bool = True) -> dict:
    """PUBLIC SEARCH: the widest no-login search. Reads the open job boards (Remotive, Arbeitnow, Remote OK,
    Himalayas) AND every company on the career-site list in one go, keeps only fresh jobs, merges repeats,
    and hands back candidates for YOU to judge. Safe to run without asking (public sources, not LinkedIn).

    Recall is deliberately wide and judging is your job: read each candidate's snippet, drop the ones that don't
    fit the user (wrong field, wrong seniority, wrong place, eligibility), then rank what's left. Call get_job
    with the file and row for the full description before recommending a job.
    query: the job title words ("civil engineer"); call again with other titles for a wider net.
    location: the user's city ("Houston"); local jobs are listed first. include_remote: add remote jobs after them.
    days: only jobs posted in the last N days (default 7). limit: candidates returned (max 60).
    web: also search the whole web for employers hiring for this title (any company on Greenhouse, Lever, Ashby,
    Workable or SmartRecruiters, not just the saved list). Slower the first time; leave on.
    """
    import open_sources
    logs = []
    path = open_sources.run(query, location, log=lambda m: (logs.append(m), _log(m)), days=days,
                            remote_ok=include_remote, web=web)
    if not path:
        return {"ok": False, "hint": "No fresh matches for this title. Try ONE broader title or a wider location, "
                                     "but don't repeat many near-identical searches.", "log": logs[-8:]}
    rows = [dict(r, row=i) for i, r in enumerate(_rows(Path(path)))]
    local = [r for r in rows if location and job_boards.location_matches(r["Location"], "", location)]
    rows = local + [r for r in rows if r not in local]
    per_company, picked = {}, []
    for r in rows:      # a few employers post dozens of near-identical roles; keep the list varied
        n = per_company[r["Company"]] = per_company.get(r["Company"], 0) + 1
        if n <= 5:
            picked.append(r)
    cands = [{"row": r["row"], "title": r["Title"], "company": r["Company"], "location": r["Location"],
              "remote": r["Workplace"] == "Remote", "posted": r["Posted"], "source": r["Apply type"],
              "link": r["Job URL"], "snippet": str(r["Description"])[:350]} for r in picked[:min(limit, 60)]]
    return {"ok": True, "file": Path(path).name, "found": len(rows), "candidates": cands, "log": logs[-8:]}


@mcp.tool(annotations=READ_ONLY)
def job_hunt_guide() -> str:
    """Read this FIRST when the user wants you to find jobs. It is the step-by-step plan."""
    return f"""JOB HUNT PLAN (LinkedIn only). The user runs the LinkedIn search in their app. You never search for jobs
yourself and you have no search tool: you read their resume, set up the search, then judge the results.

1. RESUME. If the user gave you a resume in this chat, call save_master_resume with its exact text. Otherwise call
   get_master_resume (none saved: ask them to upload it).
*** IMPORTANT: EVERY time the user asks you to find or search for jobs, you MUST call set_search_plan again, even
if a plan or old results already exist. That call is the ONLY thing that starts the LinkedIn searches (the first
3 titles, one after another). Reading old results or tailoring a resume does not search anything. ***
2. SET UP THE SEARCH. Work out 3-6 target job titles (best first) and 8-15 specific keywords (tools, software,
   methods, certifications) from the resume, plus the US city they want. Call set_search_plan with them. Ask a
   question only if something important is unclear (class year, remote OK, visa).
3. set_search_plan also makes the app open Chrome and search LinkedIn for your FIRST 3 titles, one after another. Do NOT stop and do NOT
   ask the user to tell you when it is done: call list_results straight away. It waits for the search, and
   returns "search": "running" while it is still going; keep calling it until "search" is "done" (the 3 searches
   take about 10-20 minutes, so up to about 40 calls). NEVER end your turn or report "still running" while it says
   running: the user is not there to say "continue", so you keep calling until it is done. Say one short line to the user first ("Search started, I'll watch it"). Never look for
   jobs any other way, and never ask them to run it faster or more often.
4. WHEN list_results SAYS "done": read EVERY file listed in just_finished (one per title) with read_results using
   include_description=true and limit=10 (page with offset). That one call returns every row's description
   (first 1500 characters), so judge straight from it. DO NOT call get_job on each row: it is slow for the user.
   Use get_job only for a final one or two jobs where 1500 characters weren't enough. Judge each against the
   resume: right field and level, skills they have, eligibility (class year, citizenship, location), freshness.
5. SAVE THE WINNERS with save_findings: only jobs you read, each with its link (the Job URL), a fit
   score 0-100 and a one-line why. Never invent a posting, link, deadline or requirement; leave unknowns blank.
6. REPORT: the top 10 by fit with company, role, location, why it fits, any eligibility problem, and the link.
   Keep chat messages short.
7. TAILORING PACK (this is the last time you use this connector for the search). Right after the report, write in
   the chat, for each of the top 5 jobs: title, company, link, 10-15 keywords and phrases copied EXACTLY from its
   description (tools, methods, skills, certifications), and any eligibility notes. Also keep the master resume
   text in the chat. Then tell the user: "Search is done. Say 'tailor my resume' and I'll continue without the
   connector."

PHASE 2: TAILORING. When the user asks you to update or tailor the resume (this chat or a new one):
- Do NOT call any connector tool. No get_master_resume, list_results, read_results, get_job or set_search_plan.
  They add approvals and re-run the search stage. Work only from the resume text, the tailoring pack and the
  job text already in this chat. If something is missing, ask the user to paste it; never fetch it.
- Never call set_search_plan in this phase: it starts new LinkedIn searches.
- Steps: (a) pick the keywords from the pack that are TRUE for the user and work them into the resume in the
  posting's own wording; (b) fix grammar and tighten every line; (c) keep only the strongest activities and
  projects for that job, drop the unrelated ones; (d) never invent facts, numbers or skills; (e) one page.
- If you have a Gamma tool, send it the final resume with a prompt asking for a clean, professional, one-page CV
  (simple layout, readable fonts, no graphics that break applicant-tracking systems). If you have no Gamma tool,
  say so and give the final text for the user to paste into Gamma.
- Return the finished resume to the user in the chat, with the link to the Gamma file if there is one.
If anything fails, call diagnose. Resume folder: {PROFILE_DIR}
"""


@mcp.tool()
@_logged
def hunt_status(batch: int = 8) -> dict:
    """Where the job hunt stands: companies checked/pending, the next batch to check, jobs saved so far."""
    import hunt
    return hunt.status(batch)


@mcp.tool()
@_logged
def queue_companies(companies: list[dict]) -> dict:
    """Add target employers to the hunt's to-do list. Each item: {"name": "WGI", "tier": "target|reach|safe",
    "why": "short reason it fits this user"}. Duplicates are ignored."""
    import hunt
    return {"added": hunt.queue_companies(companies), **hunt.status(0)}


@mcp.tool()
@_logged
def save_findings(jobs: list[dict], checked: list[dict] = []) -> dict:
    """Save jobs you VERIFIED (opened the real posting) into the Excel file job_hunt.xlsx, ranked by fit.

    jobs: [{"title","company","link" (required, the real posting),"location","type","term" (e.g. Summer 2027),
    "deadline","eligibility" (can the user apply? class year/GPA/citizenship),"why" (fit in one line),
    "requirements","fit" (0-100)}]. checked: [{"name": company, "note": "none open yet"}] for every company
    you looked at. Links that can't be confirmed come back in link_warnings; re-check and resend those.
    """
    import hunt
    return hunt.save_findings(jobs, checked)


@mcp.tool()
async def find_jobs_for_me(titles: list[str], skills: list[str] = [], location: str = "",
                           use_open_sources: bool = True) -> dict:
    """Search for jobs from a resume: several titles at once across the career-site list and the open
    feeds, de-duplicated and ranked by how many of `skills` each posting mentions. Read job_hunt_guide
    first. Safe to run without asking (public sources, not LinkedIn).

    titles: 1-8 short job titles. skills: exact skills/tools from the resume. location: "City, Country" or "Remote".
    """
    import asyncio
    return await asyncio.to_thread(_find_jobs, titles, skills, location, use_open_sources)


@_logged
def _find_jobs(titles, skills, location, use_open_sources):
    import job_hunt
    logs = []
    path, ranked = job_hunt.find(titles, skills, location, use_open_sources, lambda m: (logs.append(m), _log(m)))
    if not path:
        return {"ok": False, "log": logs[-8:]}
    top = [{"match": r[0], "skills": r[1], "title": r[2], "company": r[3], "location": r[4], "source": r[12],
            "posted": r[7], "link": r[14]} for r in ranked[:15]]
    return {"ok": True, "file": Path(path).name, "rows": len(ranked), "top": top, "log": logs[-8:]}


@mcp.tool()
def list_companies() -> list[dict]:
    """The companies search_job_boards checks (platform, id, name)."""
    return [{"platform": job_boards.PLATFORMS[p], "id": s, "name": n} for p, s, n in job_boards.load_companies()]


@mcp.tool()
@_logged
def add_company(name_or_url: str) -> dict:
    """Add a company to the career-site list, by name ("Stripe") or by a link to any of its job
    postings. A name is tried on all 5 platforms; companies on Workday, iCIMS etc. can't be added.
    """
    logs = []
    added = job_boards.add(name_or_url, log=logs.append)
    return {"added": [{"platform": job_boards.PLATFORMS[p], "id": s} for p, s, _ in added], "log": logs}


def _read_doc(path):
    if path.suffix.lower() == ".docx":
        return "\n".join(p.text for p in Document(path).paragraphs)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(encoding="utf-8", errors="replace")


@mcp.tool()
@_logged
def save_master_resume(text: str) -> dict:
    """Save the resume the user uploaded to you (as plain text or Markdown) so the app and later chats can read it.

    Call this once after the user gives you their resume, then get_master_resume returns it. Copy the text
    faithfully: do not reword, summarise or add anything.
    """
    PROFILE_DIR.mkdir(exist_ok=True)
    if len(text.strip()) < 50:
        return {"ok": False, "error": "That looks too short to be a resume."}
    path = PROFILE_DIR / "resume_from_go.md"
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "saved": path.name, "chars": len(text)}


@mcp.tool()
@_logged
def set_search_plan(titles: list[str], keywords: list[str], location: str = "", note: str = "") -> dict:
    """Put what you found in the resume into the app's screen, as the user's LinkedIn search plan.

    titles: 3-6 job titles to search ("Civil Engineer", "Project Engineer"), best first.
    keywords: 8-15 specific skills/tools from the resume ("Civil 3D", "AutoCAD", "stormwater").
    location: the US city the user wants ("Houston, TX"). note: one short line on why (optional).
    Saving the plan also makes the app open Chrome and search LinkedIn for the first 3 titles, one after
    another; LinkedIn runs only in the app, never from here. Put the best titles first.
    """
    import json
    clean = lambda items, n: [str(i).strip()[:60] for i in items if str(i).strip()][:n]
    plan = {"titles": clean(titles, 6), "keywords": clean(keywords, 15), "location": location.strip()[:60],
            "note": note.strip()[:200], "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if not plan["titles"]:
        return {"ok": False, "error": "Give at least one job title."}
    bot.PLAN_FILE.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    # Saving the plan also asks the app to search LinkedIn for the top titles, one after another (the app does the search itself).
    search = _queue_searches(plan["titles"], plan["location"])
    return {"ok": True, **plan, "search": search,
            "next": search.get("next") or "Tell the user to click a title in the app, then Start."}


def _search_state():
    """running / done / idle for the search the app is doing for the AI. A stale or never-started one is idle."""
    import json
    import time
    try:
        st = json.loads(bot.SEARCH_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "idle", {}
    age = time.time() - st.get("time", 0)
    if st.get("state") == "queued" and age < 90:
        return "running", st            # the app picks a request up within seconds
    if st.get("state") == "running" and age < 3600:
        return "running", st
    return ("done" if st.get("state") == "done" else "idle"), st


@mcp.tool()
@_logged
def request_linkedin_search(title: str, location: str = "", pages: int = 2) -> dict:
    """Ask the app to open Chrome and run a LinkedIn jobs search. This only sends the request.

    The app does the search itself, at its own speed, and only while it is open; you cannot touch LinkedIn
    from here, change the speed, or run more than one search at a time. Max 3 pages.
    title: one job title, ideally from the search plan. location: the US city ("Houston, TX").
    Afterwards, wait for the user to say it finished, then use list_results / read_results.
    """
    return _queue_search(title, location, pages)


def _queue_search(title, location="", pages=2):
    return _queue_searches([title], location, pages)


def _queue_searches(titles, location="", pages=2):
    import json
    import time
    titles = [t.strip()[:60] for t in titles if t.strip()][:bot.AI_MAX_TITLES]
    if not titles:
        return {"ok": False, "error": "Give a job title."}
    try:
        alive = time.time() - bot.APP_ALIVE_FILE.stat().st_mtime < 10
    except OSError:
        alive = False
    if not alive:
        return {"ok": False, "error": "The app isn't open. Ask the user to open it, then try again."}
    pages = max(1, min(int(pages), bot.AI_MAX_PAGES))
    bot.SEARCH_REQUEST_FILE.write_text(json.dumps({
        "time": time.time(),
        "searches": [{"query": t, "location": location.strip()[:60], "pages": pages} for t in titles]}),
        encoding="utf-8")
    bot.SEARCH_STATUS_FILE.write_text(json.dumps({"state": "queued", "time": time.time()}), encoding="utf-8")
    return {"ok": True, "titles": titles,
            "next": f"The app will search LinkedIn for {len(titles)} title(s) one after another (several minutes). "
                    "Do NOT stop or ask the user: call list_results now and keep calling it until it says done."}


@mcp.tool(annotations=READ_ONLY)
@_logged
def get_master_resume() -> dict:
    """Read the user's master resume from the profile folder (.docx, .pdf, .md or .txt).

    Tailored resumes must only use facts from this text: reword and reorder, never invent.
    """
    PROFILE_DIR.mkdir(exist_ok=True)
    files = [f for f in PROFILE_DIR.iterdir() if f.suffix.lower() in (".docx", ".pdf", ".md", ".txt")
             and not f.name.startswith(("~$", "README"))]
    if not files:
        return {"ok": False, "error": f"No resume found. Ask the user to put their resume in {PROFILE_DIR}"}
    newest = max(files, key=lambda p: p.stat().st_mtime)
    return {"ok": True, "file": newest.name, "text": _read_doc(newest)}


def _add_runs(par, text):
    # **bold** support inside a line
    for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if part:
            par.add_run(part).bold = i % 2 == 1


def _markdown_to_docx(md, path):
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10.5)
    for line in md.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("# "):
            doc.add_heading(s[2:], level=0)
        elif s.startswith("## "):
            doc.add_heading(s[3:], level=1)
        elif s.startswith("### "):
            doc.add_heading(s[4:], level=2)
        elif s.startswith(("- ", "* ")):
            _add_runs(doc.add_paragraph(style="List Bullet"), s[2:])
        else:
            _add_runs(doc.add_paragraph(), s)
    doc.save(path)


@mcp.tool()
@_logged
def save_tailored_resume(company: str, job_title: str, resume_markdown: str,
                         cover_letter_markdown: str = "") -> dict:
    """Save a tailored resume (and optional cover letter) as Word files in the resumes folder.

    Write Markdown: '# Name', '## Section', '### Role', '- bullet', **bold**.
    """
    RESUME_DIR.mkdir(exist_ok=True)
    safe = lambda s: re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:40]
    base = f"Resume_{safe(company)}_{safe(job_title)}"
    saved = []
    resume_path = RESUME_DIR / f"{base}.docx"
    _markdown_to_docx(resume_markdown, resume_path)
    saved.append(str(resume_path))
    if cover_letter_markdown.strip():
        cl_path = RESUME_DIR / f"CoverLetter_{safe(company)}_{safe(job_title)}.docx"
        _markdown_to_docx(cover_letter_markdown, cl_path)
        saved.append(str(cl_path))
    return {"ok": True, "saved": saved}


@mcp.tool()
def diagnose() -> dict:
    """Call this first when something fails or looks wrong. Reports the results, resume and company files, and the last tool calls and errors (the activity log),
    plus fixes for the usual problems. Safe to run any time; it changes nothing.
    """
    try:
        tail = ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()[-40:]
    except OSError:
        tail = []
    files = lambda d, pat: sorted(p.name for p in d.glob(pat)) if d.exists() else []
    return {
        "results_files": files(bot.OUTPUT_DIR, "*.xlsx")[-10:],
        "master_resume_files": [n for n in files(PROFILE_DIR, "*") if not n.startswith(("README", "~$"))],
        "tailored_resumes": files(RESUME_DIR, "*.docx")[-10:],
        "companies_on_list": len(job_boards.load_companies()),
        "recent_activity": tail,
        "fixes": {
            "No resume found": f"Ask the user to put their resume (.docx/.pdf/.md/.txt) in {PROFILE_DIR}.",
            "Company not found by add_company": "Workday/iCIMS sites aren't supported; ask for a link to one of its Greenhouse/Lever/Ashby/Workable/SmartRecruiters postings.",
            "Connector says tool is missing or the URL stopped working": "The app must be open with web mode started; the public URL changes each time the app restarts, so the user must re-add the connector with the new URL.",
        },
    }


# The AI gets no search tools: jobs come only from the LinkedIn search the user runs in the app. The functions
# stay in this file (public_search, search_job_boards, find_jobs_for_me, add_company...) in case they come back.
for _name in ("public_search", "search_job_boards", "find_jobs_for_me", "list_companies", "add_company",
              "hunt_status", "queue_companies"):
    mcp.remove_tool(_name)


# ---------- web mode (for apps that need a URL, like Superhuman Go) ----------

WEB_CONFIG = HERE / "mcp_web.json"      # port and secret; git-ignored
WEB_TOOLS = [job_hunt_guide, save_master_resume, get_master_resume, set_search_plan, request_linkedin_search,
             list_results, read_results, get_job, save_findings, save_tailored_resume,
             diagnose]      # LinkedIn runs only in the app; request_linkedin_search just asks it to


def web_settings():
    """Port and secret for web mode, made once and kept. The secret in the URL works like a password."""
    import json
    import secrets
    try:
        cfg = json.loads(WEB_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if not cfg.get("secret"):
        cfg = {**cfg, "port": cfg.get("port", 8765), "secret": secrets.token_urlsafe(18)}
        WEB_CONFIG.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    return cfg


def web_url(host="localhost"):
    cfg = web_settings()
    return f"http://{host}:{cfg['port']}/mcp/{cfg['secret']}"


def web_server(allowed_hosts=()):
    """The same tools minus LinkedIn, over HTTP on this PC only (127.0.0.1)."""
    from mcp.server.transport_security import TransportSecuritySettings
    cfg = web_settings()
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", *allowed_hosts]
    web = FastMCP("job-alert", host="127.0.0.1", port=cfg["port"], streamable_http_path=f"/mcp/{cfg['secret']}",
                  stateless_http=True, log_level="WARNING", transport_security=TransportSecuritySettings(
                      enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                      allowed_origins=[f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts]))
    for fn in WEB_TOOLS:
        web.add_tool(fn)
    return web


if __name__ == "__main__":
    if "--web" in sys.argv:
        _log(f"Web mode: {web_url()}")
        web_server().run("streamable-http")
    else:
        mcp.run()
