"""LinkedIn public-data collector.

Reads LinkedIn search results at human speed and saves them to Excel.
Read-only: never clicks Connect, Message, Follow or anything that changes your account.
For jobs it clicks each job card to open its details (same as you browsing), and only
clicks Apply if you turn on --grab-apply-links (it opens the company site, then closes it).

Usage:
    1. Run start_chrome.bat and make sure you're logged into LinkedIn in that window.
    2. python linkedin_bot.py people "marketing manager mumbai"
       python linkedin_bot.py companies "solar energy" --pages 5
       python linkedin_bot.py jobs "python developer" --location "Bangalore"
"""

import argparse
import random
import re
import socket
import threading
import time
import winsound
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

from openpyxl import Workbook
from openpyxl.styles import Font
from playwright.sync_api import sync_playwright

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"     # not "localhost": Windows tries IPv6 first and stalls
OUTPUT_DIR = Path(__file__).parent / "output"
PLAN_FILE = Path(__file__).parent / "search_plan.json"     # what the AI read from the resume: titles, keywords, place

# Safety limits so LinkedIn doesn't flag the account.
SEARCH_REQUEST_FILE = Path(__file__).parent / "search_request.json"    # the AI asks; only the app runs LinkedIn
APP_ALIVE_FILE = Path(__file__).parent / "app_alive.txt"               # touched by the open app every 2 seconds
SEARCH_STATUS_FILE = Path(__file__).parent / "search_status.json"      # queued / running / done, for the AI to wait on
AI_MAX_TITLES = 3           # most job titles an AI request may search, one after another
AI_MAX_PAGES = 3            # most pages an AI request may ask for
MAX_PAGES = 10             # ~100 people/companies per run
MAX_JOBS = 100              # each job is a click + panel load, so cap these separately
# Speed levels: random (min, max) seconds for each kind of pause.
# Faster = more requests per minute = more chance LinkedIn notices.
SPEEDS = {
    "low": {        # ~3s per action, safest
        "load": (5, 8), "scroll": (2, 4), "read": (2.5, 3.5), "page": (20, 40),
    },
    "medium": {     # ~1s per action, still careful
        "load": (3, 6), "scroll": (0.8, 1.5), "read": (0.8, 1.2), "page": (8, 20),
    },
    "high": {       # quick, highest risk
        "load": (1.5, 3), "scroll": (0.3, 0.6), "read": (0.15, 0.35), "page": (3, 6),
    },
}

BLOCK_MARKERS = ("/checkpoint/", "/authwall", "/login", "/uas/")

COLUMNS = {
    "people": ["Name", "Headline", "Location", "Connection", "Profile URL"],
    "companies": ["Name", "Industry / Location", "Followers", "Company URL"],
    "jobs": ["Title", "Company", "Location", "Workplace", "Type", "Posted", "Applicants", "Salary",
             "Industry", "Company size", "Apply type", "Apply link", "Job URL", "Description"],
}

# Which column holds the LinkedIn link (the app opens it on double-click).
LINK_COLUMN = {"people": "Profile URL", "companies": "Company URL", "jobs": "Job URL"}

# LinkedIn serves two jobs pages and switches accounts between them:
#   new     (/jobs/search-results/): no normal links on cards; the job ID is in componentkey.
#   classic (/jobs/search/):         cards are <li data-occludable-job-id="ID">.
# Returns [[jobId, cardSelector], ...] for whichever layout is on screen.
JOB_IDS_JS = r"""() => {
  const out = new Map();
  for (const e of document.querySelectorAll('[componentkey^="job-card-component-ref-"]')) {
    const id = e.getAttribute('componentkey').split('-').pop();
    out.set(id, `[componentkey="job-card-component-ref-${id}"]`);
  }
  for (const e of document.querySelectorAll('[data-occludable-job-id]')) {
    const id = e.getAttribute('data-occludable-job-id');
    if (!out.has(id)) out.set(id, `[data-occludable-job-id="${id}"]`);
  }
  return [...out.entries()];
}"""

# The job's title link in the details panel. Classic cards also link to /jobs/view/<id>, so links
# inside a card don't count.
PANEL_LINK_JS = r"""
const panelLink = (jobId) => [...document.querySelectorAll('a[href*="/jobs/view/"]')].find(a =>
  a.href.includes('/jobs/view/' + jobId) &&
  !a.closest('[data-occludable-job-id], [data-job-id], [componentkey^="job-card-component-ref-"]'));
"""

# Reads the job details panel on the right. Returns null until it shows the job we clicked.
JOB_PANEL_JS = r"""
(jobId) => {
""" + PANEL_LINK_JS + r"""
  const t = panelLink(jobId);
  if (!t) return null;
  let panel = t;
  while (panel.parentElement && !/About the job/.test(panel.innerText)) panel = panel.parentElement;
  if (panel === document.body || panel === document.documentElement) return null;   // description not loaded yet
  const lines = el => el.innerText.split('\n').map(s => s.trim()).filter(Boolean);
  const section = name => {
    const h = [...panel.querySelectorAll('h2')].find(h => h.innerText.trim() === name);
    if (!h) return [];
    let el = h;
    while (el.parentElement && el.parentElement !== panel && el.parentElement.innerText.length < h.innerText.length + 40)
      el = el.parentElement;
    return lines(el.parentElement || el).filter(l => l !== name);
  };
  for (const e of document.querySelectorAll('[data-bot-apply]')) e.removeAttribute('data-bot-apply');
  const apply = [...panel.querySelectorAll('a, button')].find(e =>
    /^(easy apply|apply)$/i.test(e.innerText.trim()) || /\bapply\b/i.test(e.getAttribute('aria-label') || ''));
  if (apply) apply.setAttribute('data-bot-apply', '1');
  return {
    title: t.innerText.trim(),
    header: lines(panel),
    about: section('About the job'),
    company: section('About the company'),
    apply: apply ? {text: apply.innerText.trim(), label: apply.getAttribute('aria-label') || '',
                    href: apply.tagName === 'A' ? apply.href : ''} : null,
  };
}
"""

# "About the company" only renders once the details panel is scrolled down to it.
PANEL_SCROLL_JS = r"""
([jobId, to]) => {
""" + PANEL_LINK_JS + r"""
  let el = panelLink(jobId);
  while (el && !(/(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight)) el = el.parentElement;
  if (el) el.scrollTo({top: to === 'bottom' ? el.scrollHeight : 0, behavior: 'smooth'});
}
"""

HEADER_STOP = ("easy apply", "apply", "save", "people you can reach out to", "meet the hiring team",
               "about the job", "your profile")
# Button labels around the title on the classic jobs page; they'd otherwise be read as the company.
HEADER_SKIP = ("share", "show more options", "more options", "promoted", "promoted by hirer", "saved")
# Separators: · and • on the new page, runs of spaces (often non-breaking) on the classic one.
SEPARATORS = r"[·•]|\s{2,}"
WORKPLACES = ("On-site", "Remote", "Hybrid")
JOB_TYPES = ("Full-time", "Part-time", "Contract", "Temporary", "Internship", "Volunteer", "Other")

# A result's card is the biggest box around its link that holds no other result's link.
# Matches on link patterns rather than class names, because LinkedIn renames its CSS often.
CARD_JS = r"""
const norm = h => h.split('?')[0].replace(/\/$/, '');
const RESULT_LINKS = 'a[href*="/in/"], a[href*="/company/"], a[href*="/jobs/view/"]';
const cardFor = (a) => {
  const url = norm(a.href);
  let el = a;
  while (el.parentElement && el.parentElement.tagName !== 'MAIN' && el.parentElement !== document.body &&
         ![...el.parentElement.querySelectorAll(RESULT_LINKS)].some(x => norm(x.href) !== url)) {
    el = el.parentElement;
  }
  return el;
};
"""

# Pulls the visible text of each result card.
EXTRACT_JS = r"""
(kind) => {
""" + CARD_JS + r"""
  const pattern = {people: '/in/', companies: '/company/', jobs: '/jobs/view/'}[kind];
  const root = document.querySelector('main') || document.body;
  const seen = new Map();
  for (const a of root.querySelectorAll(`a[href*="${pattern}"]`)) {
    const url = norm(a.href);
    if (seen.has(url)) continue;
    const card = cardFor(a);
    if (!card) continue;
    const lines = card.innerText.split('\n').map(s => s.trim()).filter(Boolean);
    if (lines.length) seen.set(url, lines);
  }
  return [...seen.entries()].map(([url, lines]) => ({url, lines}));
}
"""

# People LinkedIn won't show you appear as "LinkedIn Member" cards with no profile link.
HIDDEN_MEMBERS_JS = r"""() => [...(document.querySelector('main') || document.body).querySelectorAll('span, a, div')]
  .filter(e => e.children.length === 0 && e.textContent.trim() === 'LinkedIn Member').length"""

# Puts a "Bot reading" pill at the top of the LinkedIn tab and outlines the card being read,
# so you can see exactly what it's looking at.
OVERLAY_JS = r"""
([text, url, selector]) => {
""" + CARD_JS + r"""
  let bar = document.getElementById('__botbar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = '__botbar';
    bar.style.cssText = 'position:fixed;top:12px;left:50%;transform:translateX(-50%);z-index:2147483647;' +
      'background:#0a66c2;color:#fff;font:600 14px "Segoe UI",sans-serif;padding:8px 18px;border-radius:20px;' +
      'box-shadow:0 4px 14px rgba(0,0,0,.3);pointer-events:none;max-width:80vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
    document.body.appendChild(bar);
  }
  bar.textContent = '\u{1F916} Bot reading · ' + text;
  for (const el of document.querySelectorAll('.__botread')) {
    el.classList.remove('__botread'); el.style.outline = ''; el.style.background = '';
  }
  let card = selector && document.querySelector(selector);
  if (!card && url) {
    const root = document.querySelector('main') || document.body;
    const a = [...root.querySelectorAll('a[href]')].find(a => norm(a.href) === url);
    card = a && cardFor(a);
  }
  if (card) {
    card.classList.add('__botread');
    card.style.outline = '3px solid #0a66c2';
    card.style.background = 'rgba(10,102,194,.10)';
    card.scrollIntoView({block: 'center', behavior: 'smooth'});
  }
}
"""

NOISE = {
    "connect", "message", "follow", "following", "pending", "save", "apply",
    "easy apply", "promoted", "viewed", "actively recruiting", "dismiss",
}


def clean(lines):
    out = []
    for line in lines:
        low = line.lower()
        if low in NOISE or low.startswith(("view ", "status is", "• ")) or low.endswith("’s profile"):
            continue
        if low.startswith("current:") or low.startswith("past:"):
            continue
        if out and out[-1] == line:     # LinkedIn often repeats the name for screen readers
            continue
        out.append(line)
    return out


def parse(kind, url, lines):
    degree = next((l.lstrip("• ") for l in lines if l.lstrip("• ").rstrip("+") in ("1st", "2nd", "3rd")), "")
    lines = clean(lines)
    get = lambda i: lines[i] if i < len(lines) else ""
    if kind == "people":
        rest = [l for l in lines if l.rstrip("+") not in ("1st", "2nd", "3rd")]
        g = lambda i: rest[i] if i < len(rest) else ""
        return [g(0), g(1), g(2), degree, url]
    if kind == "companies":
        followers = next((l for l in lines if "follower" in l.lower()), "")
        rest = [l for l in lines if l != followers]
        return [get(0), rest[1] if len(rest) > 1 else "", followers, url]
    return [get(0), get(1), get(2), " | ".join(lines[3:6]), url]


def unwrap(href):
    # External links go through linkedin.com/safety/go?url=<real link>
    if "/safety/go" in href:
        return parse_qs(urlparse(href).query).get("url", [href])[0]
    return href


def parse_job(job_id, d):
    # Header lines look like: company, title, "Houston, TX (On-site) · 2 weeks ago · 6 applicants",
    # salary, chips... LinkedIn uses both · and • as separators, and the order varies.
    header = []
    for line in d["header"]:
        if line.lower() in HEADER_STOP:
            break
        if line.lower() in HEADER_SKIP:
            continue
        header += [p.strip() for p in re.split(SEPARATORS, line) if p.strip()]
    ti = header.index(d["title"]) if d["title"] in header else 0
    company = header[ti - 1] if ti >= 1 else ""
    after = [x for x in header[ti + 1:] if x != company]
    pick = lambda test: next((x for x in header if test(x)), "")
    posted = pick(lambda x: "ago" in x or x.lower().startswith("reposted"))
    applicants = pick(lambda x: "applicant" in x.lower() or "clicked apply" in x.lower())
    salary = pick(lambda x: re.search(r"[$₹€£]|/yr|/hr|/mo", x))
    job_type = pick(lambda x: x in JOB_TYPES)
    location = next((x for x in after if x not in (posted, applicants, salary, job_type)
                     and x not in WORKPLACES), "")
    workplace = pick(lambda x: x in WORKPLACES)
    m = re.search(r"\((On-site|Remote|Hybrid)\)", location)      # "Houston, TX (On-site)"
    if m:
        workplace = workplace or m.group(1)
        location = location[:m.start()].strip()

    # "About the company": name, followers, Follow, Industry, •, 11-50 employees, •, 62 on LinkedIn, ...
    industry = size = ""
    comp = d["company"]
    if "Follow" in comp or "Following" in comp:
        i = comp.index("Follow") if "Follow" in comp else comp.index("Following")
        parts = [p.strip() for line in comp[i + 1:i + 6] for p in re.split(SEPARATORS, line) if p.strip()]
        size = next((p for p in parts if "employees" in p), "")
        if parts and parts[0] != size and "on LinkedIn" not in parts[0]:
            industry = parts[0]
    if not company or company.lower() in HEADER_SKIP:
        company = comp[0] if comp else ""     # "About the company" starts with the name

    desc = "\n".join(l for l in d["about"] if l.lower() not in ("show more", "show less", "… more", "...more"))

    apply = d["apply"]
    if not apply:
        apply_type, apply_link = "Not shown", ""
    elif "easy apply" in (apply["text"] + apply["label"]).lower():
        apply_type, apply_link = "Easy Apply", ""
    else:
        apply_type, apply_link = "Company site", unwrap(apply["href"])

    job_url = f"https://www.linkedin.com/jobs/view/{job_id}"
    return [d["title"], company, location, workplace, job_type, posted, applicants, salary,
            industry, size, apply_type, apply_link, job_url, desc[:32000]]   # Excel's cell limit


def grab_apply_link(page):
    """Clicks Apply, reads the company-site address from the tab it opens, closes that tab."""
    url = ""
    try:
        with page.context.expect_page(timeout=8000) as info:
            page.locator("[data-bot-apply]").first.click()
        site = info.value
        try:
            site.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        url = site.url
        site.close()
    except Exception:
        pass
    page.keyboard.press("Escape")       # closes LinkedIn's "Did you apply?" prompt if it pops up
    return url if url and url != "about:blank" else ""


def search_url(kind, query, location, page, classic=False):
    q = quote_plus(query)
    if kind == "jobs":
        start = (page - 1) * 25
        if classic:
            # The classic page takes ?location= but reads "X in Y" as keywords (and adds your home country).
            loc = f"&location={quote_plus(location)}" if location else ""
            return f"https://www.linkedin.com/jobs/search/?keywords={q}{loc}&start={start}"
        # The new page ignores ?location=, but its search understands "X in Y".
        if location:
            q = quote_plus(f"{query} in {location}")
        return f"https://www.linkedin.com/jobs/search/?keywords={q}&start={start}"
    if location:
        q = quote_plus(f"{query} {location}")
    return f"https://www.linkedin.com/search/results/{kind}/?keywords={q}&page={page}"


class Stopped(Exception):
    pass


def human_pause(rng, stop):
    if stop.wait(random.uniform(*rng)):
        raise Stopped


def scroll_like_human(page, stop, delay):
    for _ in range(random.randint(5, 9)):
        page.mouse.wheel(0, random.randint(300, 700))
        human_pause(delay, stop)


def show(page, text, url=None, selector=None):
    try:
        page.evaluate(OVERLAY_JS, [text, url, selector])
    except Exception:
        pass    # page mid-navigation; the next call will redraw it


def blocked(page):
    return any(m in page.url for m in BLOCK_MARKERS)


problem = ""      # set when a run hit a security check or an empty page; an AI batch stops on it


def alert(msg, log):
    global problem
    problem = msg
    log(f"!!! {msg}")
    for _ in range(3):
        winsound.Beep(1000, 300)


ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")    # openpyxl refuses these


def save(kind, query, rows, columns=None):
    OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe_q = "".join(c if c.isalnum() else "_" for c in query)[:40]
    path = OUTPUT_DIR / f"{kind}_{safe_q}_{stamp}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = kind
    ws.append(columns or COLUMNS[kind])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([ILLEGAL_XML.sub("", v) if isinstance(v, str) else v for v in row])
    for col in ws.columns:
        width = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 12), 60)
    ws.freeze_panes = "A2"
    wb.save(path)
    return path


def chrome_running():
    # A raw socket to 127.0.0.1: on Windows a refused "localhost" HTTP request takes ~2s,
    # and this runs every few seconds while the app is open.
    try:
        socket.create_connection(("127.0.0.1", CDP_PORT), timeout=0.3).close()
        return True
    except OSError:
        return False


CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def launch_chrome(hidden=False):
    """Opens the bot's Chrome (its own profile, so the LinkedIn login is kept). Returns False if it was already open.

    hidden=True puts the window far off screen instead of using headless mode: LinkedIn can tell headless
    Chrome apart, but an ordinary window that nobody sees looks the same as any other. The extra flags stop
    Chrome from slowing down a window it thinks is covered.
    """
    import subprocess
    if chrome_running():
        return False
    args = [CHROME_EXE, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={Path(__file__).parent / 'chrome-profile'}"]
    if hidden:
        args += ["--window-position=-32000,-32000", "--window-size=1280,900", "--no-first-run",
                 "--disable-features=CalculateNativeWinOcclusion", "--disable-backgrounding-occluded-windows",
                 "--disable-renderer-backgrounding", "--disable-background-timer-throttling", "about:blank"]
    else:
        args.append("https://www.linkedin.com/")
    subprocess.Popen(args)
    return True


def wait_for_chrome(seconds=15):
    for _ in range(int(seconds * 5)):
        if chrome_running():
            return True
        time.sleep(0.2)
    return False


def page_failed(page):
    """LinkedIn's "things aren't loading" error page, which a reload usually fixes. (An empty page is just the end.)"""
    try:
        txt = page.evaluate("() => document.body.innerText")
    except Exception:
        return False
    return "things aren't loading" in txt


PAGER_JS = r"""(n) => {
  // Page numbers only exist once the end of the jobs list is on screen, so scroll the list down first.
  const cards = document.querySelectorAll('[data-occludable-job-id], [componentkey^="job-card-component-ref-"]');
  if (cards.length) cards[cards.length - 1].scrollIntoView({block: 'end'});
  const mark = el => { el.scrollIntoView({block: 'center'}); el.setAttribute('data-bot-page', '1'); };
  // The classic pager: <ul class="jobs-search-pagination__pages"> with one <li> per page number.
  const items = [...document.querySelectorAll('[class*="jobs-search-pagination__pages"] li, [class*="jobs-search-pagination__indicator"]')];
  for (const li of items) {
    if (li.innerText.trim() === String(n)) { mark(li.querySelector('button, a') || li); return 'page list'; }
  }
  for (const sel of [`[data-test-pagination-page-btn="${n}"] button`, `button[aria-label="Page ${n}"]`,
                     `a[aria-label="Page ${n}"]`, 'button[aria-label="View next page"]']) {
    const el = document.querySelector(sel);
    if (el) { mark(el); return sel; }
  }
  // A pager with page numbers but no page n: the search simply has fewer pages.
  if (items.some(li => /^\d+$/.test(li.innerText.trim()))) return 'LAST';
  return 'NONE ' + [...document.querySelectorAll('[class*="pagination"], [aria-label*="age"]')]
    .slice(0, 6).map(e => e.tagName + ':' + (e.getAttribute('aria-label') || e.className).toString().slice(0, 60)).join(' | ');
}"""


def click_page_button(page, n, t, stop, log):
    """Presses page n under the jobs list. True if it worked, so the caller doesn't need to load a URL."""
    found = ""
    for _ in range(4):      # the list may need a moment (and another scroll) to draw its page numbers
        try:
            found = page.evaluate(PAGER_JS, n)
        except Exception:
            found = "NONE"
        if not found.startswith("NONE"):
            break
        human_pause((1.5, 2.5), stop)
    if found == "LAST":
        return "last"
    if found.startswith("NONE"):
        log(f"  no page {n} button found ({found[:200]}); loading the address instead.")
        return False
    try:
        human_pause((1, 2), stop)
        page.locator("[data-bot-page]").first.click(timeout=5000)
    except Exception as e:
        log(f"  couldn't press page {n} ({type(e).__name__}); loading the address instead.")
        return False
    finally:
        try:
            page.evaluate("() => document.querySelectorAll('[data-bot-page]').forEach(e => e.removeAttribute('data-bot-page'))")
        except Exception:
            pass
    human_pause(t["load"], stop)
    log(f"  pressed page {n} ({found}).")
    return True


def read_jobs_page(page, n, pages, rows, seen, stop, t, log, report, grab_links):
    """Clicks through every job card on the current page. Returns how many were new."""
    new = 0
    while len(rows) < MAX_JOBS:
        todo = [j for j in page.evaluate(JOB_IDS_JS) if j[0] not in seen]   # re-read: more cards load as we go
        if not todo:
            break
        job_id, sel = todo[0]
        seen.add(job_id)
        show(page, f"page {n}/{pages} · opening job {len(rows) + 1}", selector=sel)
        try:
            page.locator(sel).first.click(timeout=5000)
        except Exception:
            continue
        d = None
        for _ in range(25):
            d = page.evaluate(JOB_PANEL_JS, job_id)
            if d:
                break
            human_pause((0.3, 0.5), stop)
        if blocked(page):
            raise Blocked
        if not d:
            log(f"  job {job_id}: details didn't load, skipped")
            continue
        page.evaluate(PANEL_SCROLL_JS, [job_id, "bottom"])     # like reading down the job
        human_pause(t["read"], stop)
        for _ in range(6):
            d = page.evaluate(JOB_PANEL_JS, job_id) or d
            if d["company"]:
                break
            human_pause((0.3, 0.5), stop)
        page.evaluate(PANEL_SCROLL_JS, [job_id, "top"])
        row = parse_job(job_id, d)
        if grab_links and row[10] == "Company site" and not row[11]:
            report("status", f"Page {n}/{pages}: getting apply link for {row[0]}")
            row[11] = grab_apply_link(page)
            human_pause(t["read"], stop)
        rows.append(row)
        new += 1
        show(page, f"page {n}/{pages} · {row[0]} @ {row[1]}  ({len(rows)} saved)", selector=sel)
        report("status", f"Page {n}/{pages}: reading {row[0]} @ {row[1]}")
        report("row", row)
    if len(rows) >= MAX_JOBS:
        log(f"Reached the {MAX_JOBS}-job safety limit for one run.")
    return new


class Blocked(Exception):
    pass


def run(kind, query, location, pages, log=print, stop=None, report=None, speed="medium",
        grab_apply_links=False):
    """Collects results and returns the saved Excel path (or None).

    report(kind, value) gets ("status", text) and ("row", row) as the bot works.
    speed is "low", "medium" or "high" (see SPEEDS).
    grab_apply_links (jobs only) clicks Apply to get company-site links LinkedIn hides.
    """
    global problem
    problem = ""
    t = SPEEDS[speed]
    stop = stop or threading.Event()
    report = report or (lambda k, v: None)
    rows, seen = [], set()
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception:
            log("Can't reach Chrome. Open Chrome with the button / start_chrome.bat, log into LinkedIn, then try again.")
            return None

        ctx = browser.contexts[0]
        page = ctx.new_page()   # own tab, so your other tabs are left alone
        classic = False     # which jobs layout LinkedIn gave this account; known after the first load
        try:
            for n in range(1, pages + 1):
                log(f"Reading page {n}/{pages} ...")
                report("status", f"Opening page {n}/{pages}")
                pressed = click_page_button(page, n, t, stop, log) if kind == "jobs" and n > 1 else False
                if pressed == "last":
                    log(f"  this search only has {n - 1} page(s) of results, so that's all of them.")
                    break
                if pressed:
                    pass        # like a person: press "2" under the list, don't jump straight to ?start=25
                else:
                    page.goto(search_url(kind, query, location, n, classic), wait_until="domcontentloaded")
                    human_pause(t["load"], stop)
                if blocked(page):
                    raise Blocked
                log(f"  opened {page.url[:140]}")
                if kind == "jobs" and n > 1:
                    for attempt in (1, 2):
                        if not page_failed(page):
                            break
                        log(f"  LinkedIn says the results aren't loading; reloading (try {attempt}/2).")
                        human_pause((6, 10), stop)
                        page.reload(wait_until="domcontentloaded")
                        human_pause(t["load"], stop)

                # The new layout redirects to /jobs/search-results/; staying on /jobs/search/ means classic.
                if kind == "jobs" and n == 1 and "/jobs/search-results" not in page.url:
                    classic = True
                    log("  LinkedIn gave the classic jobs page.")
                    if location:    # classic ignored "in <location>", so load it again the classic way
                        page.goto(search_url(kind, query, location, n, classic), wait_until="domcontentloaded")
                        human_pause(t["load"], stop)
                        if blocked(page):
                            raise Blocked

                if kind == "jobs":
                    new = read_jobs_page(page, n, pages, rows, seen, stop, t, log, report, grab_apply_links)
                else:
                    show(page, f"page {n}/{pages} · scrolling through results")
                    report("status", f"Page {n}/{pages}: scrolling through results")
                    scroll_like_human(page, stop, t["scroll"])
                    found = page.evaluate(EXTRACT_JS, kind)
                    new = 0
                    for item in found:
                        if item["url"] in seen:
                            continue
                        seen.add(item["url"])
                        row = parse(kind, item["url"], item["lines"])
                        rows.append(row)
                        new += 1
                        show(page, f"page {n}/{pages} · {row[0]}  ({len(rows)} saved)", item["url"])
                        report("status", f"Page {n}/{pages}: reading {row[0]}")
                        report("row", row)
                        human_pause(t["read"], stop)
                log(f"  {new} new (total {len(rows)})")

                if new == 0:
                    hidden = 0 if kind == "jobs" else page.evaluate(HIDDEN_MEMBERS_JS)
                    if hidden:
                        report("status", f"LinkedIn is hiding names ({hidden} 'LinkedIn Member' results)")
                        alert(f"LinkedIn shows {hidden} results as 'LinkedIn Member', with no name or profile link, "
                              "so there's nothing to save. It does this for people outside your network, and much "
                              "more often after the monthly search limit or when it's limiting the account. This "
                              "can't be worked around. Try again in a few days, or search people you're connected "
                              "to (1st/2nd).", log)
                    elif n == 1:
                        # An empty first page usually means LinkedIn is limiting or checking the account,
                        # not that nothing matches. Say so instead of a bare "no results".
                        report("status", "LinkedIn showed no results")
                        alert(f"LinkedIn showed no results on page 1 (page: {page.title()[:60]!r}). Look at the "
                              "Chrome window. If it shows a security check, a CAPTCHA or a search limit, solve it "
                              "yourself and give LinkedIn a rest for today. Company career sites still work.", log)
                    else:
                        log("No more results.")
                    break
                if kind == "jobs" and len(rows) >= MAX_JOBS:
                    break
                if n < pages:
                    log("  waiting a bit like a human...")
                    show(page, f"page {n}/{pages} done · waiting before next page  ({len(rows)} saved)")
                    report("status", f"Waiting before page {n + 1} (looks human)")
                    human_pause(t["page"], stop)
        except Blocked:
            report("status", "Stopped: LinkedIn security check")
            alert("LinkedIn wants a login or security check. Sort it out in Chrome, then rerun.", log)
        except (Stopped, KeyboardInterrupt):
            log("Stopped by you, saving what we have.")
        finally:
            try:
                page.close()
            except Exception:
                pass

    if not rows:
        log("Nothing collected. Check you're logged in and the search shows results in Chrome.")
        return None
    path = save(kind, query, rows)
    log(f"Saved {len(rows)} rows -> {path}")
    return path


def main():
    ap = argparse.ArgumentParser(description="Collect public LinkedIn search results into Excel.")
    ap.add_argument("kind", choices=COLUMNS.keys())
    ap.add_argument("query", help='what to search, e.g. "marketing manager"')
    ap.add_argument("--location", default="", help='e.g. "Mumbai"')
    ap.add_argument("--pages", type=int, default=3, help=f"result pages to read (max {MAX_PAGES})")
    ap.add_argument("--speed", choices=SPEEDS.keys(), default="medium", help="low = safest, high = fastest")
    ap.add_argument("--grab-apply-links", action="store_true",
                    help="jobs: click Apply to read company-site links LinkedIn hides (counts as an apply click)")
    args = ap.parse_args()
    run(args.kind, args.query, args.location, max(1, min(args.pages, MAX_PAGES)), speed=args.speed,
        grab_apply_links=args.grab_apply_links)


if __name__ == "__main__":
    main()
