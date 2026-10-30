"""MCP server for the LinkedIn Data Bot.

Lets Claude (Claude Code / Claude Desktop, on your normal subscription, no API key) read the
jobs the bot collected, read your master resume, and save tailored resumes as Word files.

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
from openpyxl import load_workbook

import job_boards
import linkedin_bot as bot

HERE = Path(__file__).parent
PROFILE_DIR = HERE / "profile"          # put your master resume here
RESUME_DIR = HERE / "resumes"           # tailored resumes are saved here

mcp = FastMCP("linkedin-bot")


def _log(msg):
    print(msg, file=sys.stderr, flush=True)    # stdout belongs to the MCP protocol


def _results_file(name):
    path = (bot.OUTPUT_DIR / name).resolve()
    if path.parent != bot.OUTPUT_DIR.resolve() or not path.exists():
        raise ValueError(f"No results file named {name!r}. Call list_results to see the files.")
    return path


def _rows(path):
    rows = list(load_workbook(path, read_only=True).active.iter_rows(values_only=True))
    header = [str(h) for h in rows[0]]
    return [dict(zip(header, (v if v is not None else "" for v in r))) for r in rows[1:]]


@mcp.tool()
def list_results() -> list[dict]:
    """List the Excel files the bot has saved, newest first (kind, search, date, row count)."""
    out = []
    for f in sorted(bot.OUTPUT_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True):
        kind = f.stem.split("_", 1)[0]
        out.append({"file": f.name, "kind": kind,
                    "saved": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "rows": len(_rows(f))})
    return out


@mcp.tool()
def read_results(file: str, offset: int = 0, limit: int = 25, include_description: bool = False) -> dict:
    """Read rows from a results file. Each row has a 'row' number to use with get_job.

    Descriptions are left out unless include_description is true, to keep responses small.
    """
    rows = _rows(_results_file(file))
    page = []
    for i, r in enumerate(rows[offset:offset + limit], start=offset):
        r = dict(r, row=i)
        if not include_description and "Description" in r:
            r["Description"] = f"({len(str(r['Description']))} chars, use get_job)"
        page.append(r)
    return {"total": len(rows), "offset": offset, "rows": page}


@mcp.tool()
def get_job(file: str, row: int) -> dict:
    """Get one job with its full description (row number from read_results)."""
    rows = _rows(_results_file(file))
    if not 0 <= row < len(rows):
        raise ValueError(f"row must be 0-{len(rows) - 1}")
    return dict(rows[row], row=row)


@mcp.tool()
def run_search(kind: str, query: str, location: str = "", pages: int = 1, speed: str = "medium") -> dict:
    """Run the LinkedIn bot (kind: people, companies or jobs) and save results to Excel.

    Uses the user's LinkedIn account in the bot's Chrome window, so ask the user before
    calling this. Takes a few minutes. Chrome must be open (Open Chrome in the app).
    """
    if kind not in bot.COLUMNS:
        raise ValueError("kind must be people, companies or jobs")
    if speed not in bot.SPEEDS:
        raise ValueError("speed must be low, medium or high")
    if not bot.chrome_running():
        return {"ok": False, "error": "Chrome isn't open. Ask the user to click Open Chrome in the app and log in."}
    logs = []
    path = bot.run(kind, query, location, max(1, min(pages, bot.MAX_PAGES)),
                   log=lambda m: (logs.append(m), _log(m)), speed=speed)
    if not path:
        return {"ok": False, "log": logs[-5:]}
    return {"ok": True, "file": Path(path).name, "rows": len(_rows(Path(path))), "log": logs[-5:]}


@mcp.tool()
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
def list_companies() -> list[dict]:
    """The companies search_job_boards checks (platform, id, name)."""
    return [{"platform": job_boards.PLATFORMS[p], "id": s, "name": n} for p, s, n in job_boards.load_companies()]


@mcp.tool()
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


# ---------- web mode (for apps that need a URL, like Superhuman Go) ----------

WEB_CONFIG = HERE / "mcp_web.json"      # port and secret; git-ignored
WEB_TOOLS = [list_results, read_results, get_job, search_job_boards, list_companies, add_company,
             get_master_resume, save_tailored_resume]      # run_search is left out: no LinkedIn over the web


def web_settings():
    """Port and secret for web mode, made once and kept. The secret in the URL works like a password."""
    import json
    import secrets
    try:
        cfg = json.loads(WEB_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    if not cfg.get("secret"):
        cfg = {"port": cfg.get("port", 8765), "secret": secrets.token_urlsafe(18)}
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
