"""Jobs straight from company career sites, through their public job-board APIs.

No LinkedIn, no login, no browser: Greenhouse, Lever, Ashby, Workable and SmartRecruiters
publish every company's open jobs as plain JSON meant for job boards. The catch is that each
API lists one company at a time, so you keep a list of companies in companies.txt and this
checks all of them, keeping jobs whose title matches your search.

Results use the same columns as LinkedIn jobs (Apply type = the platform), so the MCP tools
and resume tailoring work on them unchanged.

Usage:
    python job_boards.py search "civil engineer" --location "Houston"
    python job_boards.py add "Stripe"                         # finds which platform it uses
    python job_boards.py add https://jobs.lever.co/spotify    # or paste any careers link
    python job_boards.py import                               # companies from LinkedIn apply links
    python job_boards.py list
"""

import argparse
import html
import json
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

import requests
from openpyxl import load_workbook

import linkedin_bot as bot

COMPANIES_FILE = Path(__file__).parent / "companies.txt"
COLUMNS = bot.COLUMNS["jobs"]
LINK_COLUMN = "Job URL"
MAX_ROWS = 500              # per run; Excel stays readable and the tailor has plenty to pick from
SR_MAX_POSTINGS = 1000      # SmartRecruiters pages 100 at a time; big employers have thousands
COMPANY_WORKERS = 6         # companies checked at the same time
SR_DETAIL_WORKERS = 6       # SmartRecruiters job-detail requests at the same time

PLATFORMS = {
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "workable": "Workable",
    "smartrecruiters": "SmartRecruiters",
}

HEADERS = {"User-Agent": "job-alert-bot/1.0 (personal job search)"}
_http = requests.Session()
_http.headers.update(HEADERS)


class NotFound(Exception):
    pass


class Prefiltered(list):
    """Rows a fetcher already matched itself; total is how many jobs the company has open."""
    def __init__(self, rows, total):
        super().__init__(rows)
        self.total = total


def get_json(url, params=None):
    r = _http.get(url, params=params, timeout=25)
    if r.status_code == 404:
        raise NotFound(url)
    r.raise_for_status()
    return r.json()


# ---------- text helpers ----------

def to_text(markup):
    """HTML job description -> plain text with line breaks and '- ' bullets."""
    if not markup:
        return ""
    s = html.unescape(markup) if "&lt;" in markup else markup      # Greenhouse double-escapes
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[ \t\xa0]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", "\n".join(l.strip() for l in s.splitlines()))
    return s.replace("\n\n- ", "\n- ").strip()


def day(value):
    """ISO string or epoch milliseconds -> YYYY-MM-DD."""
    if not value:
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    return str(value)[:10]


def workplace_of(*hints):
    text = " ".join(str(h) for h in hints if h).lower().replace("_", "-")
    for word, label in (("hybrid", "Hybrid"), ("remote", "Remote"), ("on-site", "On-site"), ("onsite", "On-site")):
        if word in text:
            return label
    return ""


def employment(value):
    v = (value or "").replace("_", "").replace("-", "").replace(" ", "").lower()
    return {"fulltime": "Full-time", "parttime": "Part-time", "contract": "Contract", "contractor": "Contract",
            "temporary": "Temporary", "intern": "Internship", "internship": "Internship",
            "permanent": "Full-time"}.get(v, value or "")


def pretty(slug):
    return re.sub(r"[-_.]+", " ", slug).title()


def row(title, company, location, workplace, job_type, posted, salary, industry, platform, apply_link,
        job_url, description):
    # Same order as bot.COLUMNS["jobs"]; LinkedIn-only fields (Applicants, Company size) stay blank.
    description = re.sub(r"[ \t\xa0]+", " ", description or "")      # plain-text feeds carry &nbsp;s too
    return [title, company, location, workplace, job_type, posted, "", salary, industry, "",
            PLATFORMS[platform], apply_link, job_url, description[:32000]]


# ---------- one fetcher per platform: (slug, name, query, location) -> list of rows ----------
# Each returns every open job for the company; matching happens afterwards in run().

def fetch_greenhouse(slug, name, query, location, stop):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", {"content": "true"})
    out = []
    for j in data["jobs"]:
        loc = (j.get("location") or {}).get("name", "")
        dept = ", ".join(d["name"] for d in j.get("departments") or [])
        out.append(row(j["title"], j.get("company_name") or name or pretty(slug), loc, workplace_of(loc), "",
                       day(j.get("first_published") or j.get("updated_at")), "", dept, "greenhouse",
                       f"https://job-boards.greenhouse.io/{slug}/jobs/{j['id']}", j["absolute_url"],
                       to_text(j.get("content"))))
    return out


def fetch_lever(slug, name, query, location, stop):
    try:
        data = get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
    except NotFound:
        data = get_json(f"https://api.eu.lever.co/v0/postings/{slug}", {"mode": "json"})   # EU-hosted accounts
    out = []
    for j in data:
        c = j.get("categories") or {}
        locs = c.get("allLocations") or [c.get("location", "")]
        sal = j.get("salaryRange") or {}
        salary = ""
        if sal.get("min") or sal.get("max"):
            salary = f"{sal.get('currency', '')} {sal.get('min', 0):,}-{sal.get('max', 0):,} {sal.get('interval', '')}".strip()
        parts = [j.get("descriptionPlain", "")]
        for section in j.get("lists") or []:
            parts.append(f"{section.get('text', '')}\n{to_text(section.get('content'))}")
        parts.append(j.get("additionalPlain", ""))
        out.append(row(j["text"], name or pretty(slug), "; ".join(l for l in locs if l),
                       workplace_of(j.get("workplaceType"), c.get("location")), employment(c.get("commitment")),
                       day(j.get("createdAt")), salary, c.get("department") or c.get("team", ""), "lever",
                       j.get("applyUrl", ""), j["hostedUrl"], "\n\n".join(p.strip() for p in parts if p and p.strip())))
    return out


def fetch_ashby(slug, name, query, location, stop):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", {"includeCompensation": "true"})
    out = []
    for j in data["jobs"]:
        if j.get("isListed") is False:
            continue
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        comp = j.get("compensation") or {}
        out.append(row(j["title"], name or pretty(slug), "; ".join(l for l in locs if l),
                       workplace_of(j.get("workplaceType"), "remote" if j.get("isRemote") else "", *locs),
                       employment(j.get("employmentType")), day(j.get("publishedAt")),
                       comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary") or "",
                       j.get("department", ""), "ashby", j.get("applyUrl", ""), j["jobUrl"],
                       j.get("descriptionPlain") or to_text(j.get("descriptionHtml"))))
    return out


def fetch_workable(slug, name, query, location, stop):
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", {"details": "true"})
    out = []
    for j in data["jobs"]:
        locs = [", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x)
                for l in j.get("locations") or [] if not l.get("hidden")]
        if not locs:
            locs = [", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)]
        out.append(row(j["title"], data.get("name") or name or pretty(slug), "; ".join(l for l in locs if l),
                       "Remote" if j.get("telecommuting") else "", employment(j.get("employment_type")),
                       day(j.get("published_on") or j.get("created_at")), "", j.get("industry") or j.get("department", ""),
                       "workable", j.get("application_url", ""), j["url"], to_text(j.get("description"))))
    return out


def fetch_smartrecruiters(slug, name, query, location, stop):
    # The list has no descriptions, so it's narrowed with SmartRecruiters' own (loose) search and
    # each title-matching job's details are fetched one by one.
    base = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
    params = {"limit": 100, "offset": 0}
    if query:
        params["q"] = query
    total = get_json(base, {"limit": 1})["totalFound"] if (query or location) else None

    def fetch_all(extra):
        first = get_json(base, dict(params, **extra))
        found = first["content"]
        # The first page says how many there are, so the rest are fetched together.
        offsets = range(100, min(first["totalFound"], SR_MAX_POSTINGS), 100)
        with ThreadPoolExecutor(SR_DETAIL_WORKERS) as pool:
            for page in pool.map(lambda o: [] if stop.is_set() else
                                 get_json(base, dict(params, offset=o, **extra))["content"], offsets):
                found += page
        return found

    # Big employers have thousands of jobs and only SR_MAX_POSTINGS are read, so with a location the
    # filtering happens on SmartRecruiters' side: as a city ("Plymouth"), else as a region ("MI").
    places = [w.strip() for w in location.split(",") if w.strip()]
    if not places or any(p.lower() == "remote" for p in places):
        postings = fetch_all({})
    else:
        postings, ids = [], set()
        for place in places:
            for p in fetch_all({"city": place}) or fetch_all({"region": place}):
                if p["id"] not in ids:
                    ids.add(p["id"])
                    postings.append(p)

    def full_location(p):
        loc = p.get("location") or {}
        return loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)

    matches = [p for p in postings if title_matches(p["name"], query)
               and location_matches(full_location(p), "", location)][:MAX_ROWS]

    def detail(p):
        if stop.is_set():
            return None
        d = get_json(f"{base}/{p['id']}")
        sections = (d.get("jobAd") or {}).get("sections") or {}
        desc = "\n\n".join(f"{s.get('title', '')}\n{to_text(s.get('text'))}" for s in sections.values()
                           if s.get("text"))
        loc = p.get("location") or {}
        workplace = "Remote" if loc.get("remote") else "Hybrid" if loc.get("hybrid") else ""
        return row(p["name"], (p.get("company") or {}).get("name") or name or slug, full_location(p), workplace,
                   (p.get("typeOfEmployment") or {}).get("label", ""), day(p.get("releasedDate")), "",
                   (p.get("industry") or {}).get("label", ""), "smartrecruiters",
                   d.get("applyUrl", ""), d.get("postingUrl", ""), desc)

    # One request per job, so a few at a time; once Stop is pressed the rest return None straight away.
    with ThreadPoolExecutor(SR_DETAIL_WORKERS) as pool:
        out = [r for r in pool.map(detail, matches) if r]
    return Prefiltered(out, total if total is not None else len(postings))


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
    "smartrecruiters": fetch_smartrecruiters,
}


# ---------- matching ----------

def title_matches(title, query):
    """Every word of the search appears in the title ('engineer' also matches 'Engineering')."""
    t = title.lower()
    return all(w in t for w in re.findall(r"\w+", (query or "").lower()))


def location_matches(loc, workplace, wanted):
    """wanted can list several places: 'Houston, Remote'. Blank = anywhere."""
    if not wanted.strip():
        return True
    text = f"{loc} {workplace}".lower()
    return any(w.strip().lower() in text for w in wanted.split(",") if w.strip())


# ---------- company list ----------

# Careers links that reveal the platform and the company's ID on it.
BOARD_URLS = [
    ("greenhouse", r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_(?:app|board)\?for=)?([\w-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    ("lever", r"jobs(?:\.eu)?\.lever\.co/([\w.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)"),
    ("workable", r"apply\.workable\.com/(?!j/|api/)([\w-]+)"),
    ("workable", r"//(?!apply\.|www\.)([\w-]+)\.workable\.com"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([\w-]+)"),
]
NOT_SLUGS = {"embed", "jobs", "j", "api", "v1", "oneclick-ui"}


def board_from_url(url):
    """'https://jobs.lever.co/spotify/123' -> ('lever', 'spotify'), or None."""
    url = bot.unwrap(url or "")
    for platform, pattern in BOARD_URLS:
        m = re.search(pattern, url, re.I)
        if m and m.group(1).lower() not in NOT_SLUGS:
            return platform, m.group(1)
    return None


def load_companies():
    """companies.txt lines: 'platform:slug  Optional Name   # comment'."""
    out = []
    if not COMPANIES_FILE.exists():
        return out
    for line in COMPANIES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, name = line.partition(" ")
        platform, _, slug = key.partition(":")
        if platform.lower() in FETCHERS and slug:
            out.append((platform.lower(), slug, name.strip()))
    return out


def add_companies(entries):
    """Appends (platform, slug, name) entries not already listed. Returns the ones added."""
    have = {(p, s.lower()) for p, s, _ in load_companies()}
    new = [e for e in entries if (e[0], e[1].lower()) not in have]
    if not new:
        return []
    if not COMPANIES_FILE.exists():
        COMPANIES_FILE.write_text(
            "# Companies to check. One per line: platform:id  Optional Name\n"
            "# Platforms: greenhouse, lever, ashby, workable, smartrecruiters\n", encoding="utf-8")
    with COMPANIES_FILE.open("a", encoding="utf-8") as f:
        for platform, slug, name in new:
            f.write(f"{platform}:{slug}  {name}".rstrip() + "\n")
    return new


def probe(platform, slug):
    """How many jobs the company has open on that platform, or None if it isn't there."""
    try:
        if platform == "greenhouse":
            return len(get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")["jobs"])
        if platform == "lever":
            return len(get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"}))
        if platform == "ashby":
            return len(get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")["jobs"])
        if platform == "workable":
            return len(get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")["jobs"])
        if platform == "smartrecruiters":
            # Unknown IDs return 200 with nothing in them, so zero counts as not found.
            n = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", {"limit": 1})["totalFound"]
            return n or None
    except (NotFound, requests.RequestException, ValueError, KeyError):
        return None


# Their boards don't report the company's name, so a name-based match can't be double-checked.
UNNAMED_PLATFORMS = {"lever", "ashby"}

COMPANY_SUFFIXES = {"inc", "llc", "pllc", "pc", "ltd", "co", "company", "corp", "corporation", "plc", "lp", "llp",
                    "the", "group", "gmbh"}


def core_words(name):
    """'Turner Construction Company, Inc.' -> ['turner', 'construction']"""
    words = re.findall(r"[a-z0-9]+", name.lower().replace("&", " and "))
    return [w for w in words if w not in COMPANY_SUFFIXES] or words


def id_guesses(name):
    words = re.findall(r"[A-Za-z0-9]+", name)
    core = [w for w in words if w.lower() not in COMPANY_SUFFIXES] or words
    out = []
    for ws in (core, words):
        out += ["".join(ws).lower(), "-".join(ws).lower(), "".join(ws)]
    return [g for g in dict.fromkeys(out) if len(g) > 2]


def board_name(platform, slug):
    """The company name a board reports, for the platforms that report one."""
    try:
        if platform == "greenhouse":
            return get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}")["name"]
        if platform == "workable":
            return get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")["name"]
        if platform == "smartrecruiters":
            c = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", {"limit": 1})["content"]
            return c[0]["company"]["name"] if c else None
    except (NotFound, requests.RequestException, ValueError, KeyError, IndexError):
        pass
    return None


def find_company(name):
    """Guesses the company's ID on each platform. Returns [(platform, slug, open_jobs)].

    A guess only counts with at least one open job: Workable (like SmartRecruiters) answers
    unknown names with an empty board instead of "not found". Where the board reports its own
    name, it has to start with the same word, so "HDR" can't land on some other company's "hdr".
    """
    want = core_words(name)
    hits = []
    for platform in FETCHERS:
        if platform in UNNAMED_PLATFORMS and len(want) < 2:
            continue    # nothing to check a one-word guess against: Ashby's "hatch" is a software startup
        for slug in id_guesses(name):
            n = probe(platform, slug)
            if not n:
                continue
            reported = board_name(platform, slug)
            if reported and core_words(reported)[:1] != want[:1]:
                continue
            hits.append((platform, slug, n))
            break
    return hits


OTHER_SYSTEMS = {"myworkdayjobs": "Workday", "icims": "iCIMS", "oraclecloud": "Oracle", "taleo": "Taleo",
                 "paylocity": "Paylocity", "ultipro": "UKG", "ukg.net": "UKG", "avature": "Avature",
                 "jobvite": "Jobvite", "bamboohr": "BambooHR", "successfactors": "SuccessFactors"}


def add(name_or_url, log=print):
    """Adds a company by careers link or by name (tries every platform). Returns entries added."""
    is_link = "/" in name_or_url or "://" in name_or_url
    found = board_from_url(name_or_url) if is_link else None
    if is_link and not found:
        system = next((v for k, v in OTHER_SYSTEMS.items() if k in name_or_url.lower()), "a system this can't read")
        log(f"That link is on {system}, not Greenhouse, Lever, Ashby, Workable "
            "or SmartRecruiters." + (" Workday support is on the roadmap." if system == "Workday" else ""))
        return []
    if found:
        n = probe(*found)
        if n is None:
            log(f"{PLATFORMS[found[0]]} has no board called '{found[1]}'.")
            return []
        hits = [(found[0], found[1], n)]
        label = ""
    else:
        log(f"Looking for {name_or_url} on {', '.join(PLATFORMS.values())} ...")
        hits = find_company(name_or_url)
        label = name_or_url.strip()
        if not hits:
            log(f"Couldn't find '{name_or_url}' on any of them. It may use Workday, iCIMS or another system, "
                "or a different ID. Paste a link to one of its job postings instead.")
            return []
    for platform, slug, n in hits:
        log(f"  {PLATFORMS[platform]}: {slug} ({n} open jobs)")
    added = add_companies([(p, s, label) for p, s, _ in hits])
    log(f"Added {len(added)} to companies.txt." if added else "Already in companies.txt.")
    return added


LOOKUPS_FILE = Path(__file__).parent / "company_lookups.json"     # names already looked up, so re-imports are quick
LOOKUP_WORKERS = 8
NOT_COMPANIES = {"show more options", "share", "promoted", "linkedin"}     # labels an old parser saved as names


def import_from_results(log=print, files=None):
    """Adds companies from your LinkedIn job results (all of them, or just `files`):
    1. any whose apply link points at one of the 5 platforms (exact), then
    2. every other company name, looked up on all 5 platforms (a guess, checked against the board's name).
    """
    found, names, linked_names = {}, {}, set()
    for f in [Path(p) for p in files] if files else sorted(bot.OUTPUT_DIR.glob("jobs_*.xlsx")):
        try:
            rows = list(load_workbook(f, read_only=True).active.iter_rows(values_only=True))
        except Exception:
            continue
        if not rows:
            continue
        header = [str(h) for h in rows[0]]
        cols = [header.index(c) for c in ("Apply link", "Job URL") if c in header]
        company = header.index("Company") if "Company" in header else None
        for r in rows[1:]:
            cname = str(r[company] or "").strip() if company is not None else ""
            linked = False
            for i in cols:
                hit = board_from_url(str(r[i] or ""))
                if hit:
                    linked = True
                    found.setdefault((hit[0], hit[1].lower()), (hit[0], hit[1], cname))
            if linked:
                linked_names.add(cname.lower())
            elif cname and cname.lower() not in NOT_COMPANIES:
                names.setdefault(cname.lower(), cname)
    for key in linked_names:        # found through an apply link already, no need to guess
        names.pop(key, None)
    added = add_companies(list(found.values()))
    log(f"Apply links: {len(found)} companies on these platforms, {len(added)} new.")
    for p, s, n in added:
        log(f"  + {PLATFORMS[p]}: {n or s}")

    # Names: skip ones already on the list or already looked up.
    try:
        lookups = json.loads(LOOKUPS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lookups = {}
    # On the list already: matched by main word, so "Langan Engineering & Environmental Services" = "Langan".
    listed = {core_words(n or s)[0] for _, s, n in load_companies() + added if core_words(n or s)}
    todo = [n for key, n in names.items()
            if key not in lookups and core_words(n) and core_words(n)[0] not in listed]
    skipped = len(names) - len(todo)
    if not todo:
        log(f"Company names: all {len(names)} already checked or on the list.")
        return added
    log(f"Company names: looking up {len(todo)} on all 5 platforms ({skipped} already checked)...")
    new_entries, not_found = [], []
    with ThreadPoolExecutor(LOOKUP_WORKERS) as pool:
        for cname, hits in zip(todo, pool.map(find_company, todo)):
            lookups[cname.lower()] = [[p, s] for p, s, _ in hits]
            if hits:
                for p, s, n in hits:
                    check = "  (unverified: this site doesn't give the company's name, check it's the right one)" \
                        if p in UNNAMED_PLATFORMS else ""
                    log(f"  + {cname}: {PLATFORMS[p]} ({n} open jobs){check}")
                new_entries += [(p, s, cname) for p, s, _ in hits]
            else:
                not_found.append(cname)
    LOOKUPS_FILE.write_text(json.dumps(lookups, indent=1, sort_keys=True), encoding="utf-8")
    by_name = add_companies(new_entries)
    if not_found:
        log(f"  Not on these 5 platforms ({len(not_found)}, probably Workday, iCIMS etc.): " + ", ".join(not_found))
    log(f"Added {len(added) + len(by_name)} companies in total.")
    return added + by_name


# ---------- the run ----------

_company_cache = {}         # (platform, id, place) -> (time, jobs); a hunt asks the same companies many times
COMPANY_CACHE_MINUTES = 20


def fetch_company(platform, slug, name, query, location, stop):
    """One company's open jobs. Greenhouse, Lever, Ashby and Workable return everything whatever the query
    is, so a repeat within COMPANY_CACHE_MINUTES reuses it. SmartRecruiters filters by place on the server."""
    key = (platform, slug, location if platform == "smartrecruiters" else "")
    hit = _company_cache.get(key)
    if hit and time.time() - hit[0] < COMPANY_CACHE_MINUTES * 60:
        return hit[1]
    jobs = FETCHERS[platform](slug, name, query, location, stop)
    if not stop.is_set():       # a Stop part-way can leave a partial list
        _company_cache[key] = (time.time(), jobs)
    return jobs


def run(query, location="", log=print, stop=None, report=None, companies=None, seed=(), kind="boards", accept=None,
        merge=False):
    """Checks every company in companies.txt and returns the saved Excel path (or None).

    report(kind, value) gets ("status", text) and ("row", row), same as linkedin_bot.run.
    seed: rows already found elsewhere (the open sources); they come first and are saved in the same file.
    accept(row): replaces the title + location test. merge: one row per company + title (cities are joined).
    """
    stop = stop or threading.Event()
    report = report or (lambda k, v: None)
    companies = companies if companies is not None else load_companies()
    if not companies and not seed:
        log("companies.txt is empty. Add companies first (Add company, or Import from LinkedIn results).")
        return None
    rows, seen, by_title = [], set(), {}

    def is_new(r):
        # A repeat of a company + title only adds its place to the row already kept.
        if r[12] in seen:
            return False
        seen.add(r[12])
        if not merge:
            return True
        k = (re.sub(r"\W+", " ", r[0].lower()).strip(), (r[1] or "").lower())
        if k not in by_title:
            by_title[k] = r
            return True
        if r[2] and r[2] not in by_title[k][2]:
            by_title[k][2] += "; " + r[2]
        return False

    for r in seed:
        if is_new(r):
            rows.append(r)
            report("row", r)
    # Several companies at once (they're mostly on different servers). Results are handled as they
    # land, and Stop is checked every 0.2s instead of after the slowest company finishes.
    pool = ThreadPoolExecutor(COMPANY_WORKERS)
    pending = {pool.submit(fetch_company, p, s, n, query, location, stop): (p, s, n) for p, s, n in companies}
    checked, stopped_by_user = 0, False
    report("status", f"Checking {len(companies)} companies")
    try:
        while pending:
            if stop.is_set():
                stopped_by_user = True
                log("Stopped by you, saving what we have.")
                break
            done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for fut in done:
                platform, slug, name = pending.pop(fut)
                label = name or slug
                checked += 1
                report("status", f"Checked {checked}/{len(companies)}: {label} ({PLATFORMS[platform]})")
                try:
                    jobs = fut.result()
                except NotFound:
                    log(f"  {label}: no {PLATFORMS[platform]} board called '{slug}' (moved or closed?)")
                    continue
                except (requests.RequestException, ValueError, KeyError) as e:
                    log(f"  {label}: couldn't read it ({type(e).__name__})")
                    continue
                hits = [r for r in jobs if (accept(r) if accept else title_matches(r[0], query)
                                            and location_matches(r[2], r[3], location)) and is_new(r)]
                log(f"  {label}: {len(hits)} matching of {getattr(jobs, 'total', len(jobs))} open")
                for r in hits[:MAX_ROWS - len(rows)]:
                    rows.append(r)
                    report("row", r)
                if len(rows) >= MAX_ROWS:
                    log(f"Reached the {MAX_ROWS}-job limit for one run. Narrow the search to see the rest.")
                    stop.set()      # tells SmartRecruiters detail fetches still running to quit
                    pending.clear()
                    break
    finally:
        pool.shutdown(wait=False, cancel_futures=True)     # don't wait for companies still downloading
    if not rows:
        log("Stopped before any jobs came in." if stopped_by_user else
            "No matching jobs. Try fewer words in the search, a wider location, or more companies.")
        return None
    path = bot.save(kind, query or "all", rows, columns=COLUMNS)
    log(f"Saved {len(rows)} jobs -> {path}")
    return path


def main():
    ap = argparse.ArgumentParser(description="Collect jobs from company career sites (no LinkedIn).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="check every company in companies.txt")
    s.add_argument("query", nargs="?", default="", help='words that must be in the job title, e.g. "civil engineer"')
    s.add_argument("--location", default="", help='e.g. "Houston" or "Houston, Remote"')
    a = sub.add_parser("add", help="add a company by name or careers link")
    a.add_argument("name_or_url")
    sub.add_parser("import", help="add companies found in your LinkedIn results' apply links")
    sub.add_parser("list", help="show companies.txt")
    args = ap.parse_args()
    if args.cmd == "search":
        run(args.query, args.location)
    elif args.cmd == "add":
        add(args.name_or_url)
    elif args.cmd == "import":
        import_from_results()
    else:
        for p, slug, name in load_companies():
            print(f"{PLATFORMS[p]:16} {slug}  {name}")


if __name__ == "__main__":
    main()
