"""Job sources that publish a free feed made for sharing: Remotive, Arbeitnow and Remote OK.

Only sources whose terms allow this use are listed in LEGAL. Their terms ask for credit and a link
back, so every row names the source (the "Apply type" column) and keeps the link to the original
posting. Remotive asks for at most a few fetches a day, so feeds are kept on disk for CACHE_HOURS.
Anything that would break a site's rules does not belong here.
"""

import json
import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import job_boards as jb

CACHE_DIR = Path(__file__).parent / "feed_cache"
CACHE_HOURS = 6
ANYWHERE = ("worldwide", "anywhere", "global")


def _cached(key, fetch):
    CACHE_DIR.mkdir(exist_ok=True)
    f = CACHE_DIR / f"{key}.json"
    if f.exists() and time.time() - f.stat().st_mtime < CACHE_HOURS * 3600:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
    data = fetch()
    f.write_text(json.dumps(data), encoding="utf-8")
    return data


def _row(title, company, location, workplace, job_type, posted, salary, industry, source, link, desc):
    # Same order as the jobs columns; the source name goes in "Apply type" so credit is always shown.
    return [title, company, location, workplace, job_type, posted, "", salary, industry, "", source, link, link,
            (desc or "")[:32000]]


def fetch_remotive(query):
    data = _cached("remotive_" + "".join(c if c.isalnum() else "_" for c in query.lower())[:40],
                   lambda: jb.get_json("https://remotive.com/api/remote-jobs", {"search": query}))
    return [_row(j["title"], j["company_name"], j.get("candidate_required_location", ""), "Remote",
                 jb.employment(j.get("job_type")), (j.get("publication_date") or "")[:10], j.get("salary", ""),
                 j.get("category", ""), "Remotive", j["url"], jb.to_text(j.get("description")))
            for j in data.get("jobs", [])]


def fetch_arbeitnow(query):
    def pages():
        out, url = [], "https://www.arbeitnow.com/api/job-board-api"
        for _ in range(3):
            d = jb.get_json(url)
            out += d["data"]
            url = (d.get("links") or {}).get("next")
            if not url:
                break
        return out
    return [_row(j["title"], j["company_name"], j.get("location", ""), "Remote" if j.get("remote") else "",
                 ", ".join(j.get("job_types") or []), jb.day(j["created_at"] * 1000) if j.get("created_at") else "",
                 "", ", ".join(j.get("tags") or []), "Arbeitnow", j["url"], jb.to_text(j.get("description")))
            for j in _cached("arbeitnow", pages)]


def fetch_remoteok(query):
    data = _cached("remoteok", lambda: jb.get_json("https://remoteok.com/api"))
    out = []
    for j in data:
        if not j.get("position"):       # the first item is a legal notice
            continue
        pay = f"${j['salary_min']:,}-${j['salary_max']:,}" if j.get("salary_min") and j.get("salary_max") else ""
        out.append(_row(j["position"], j.get("company", ""), j.get("location", "") or "Worldwide", "Remote", "",
                        (j.get("date") or "")[:10], pay, ", ".join(j.get("tags") or []), "Remote OK",
                        j.get("url") or f"https://remoteok.com/remote-jobs/{j.get('slug', '')}",
                        jb.to_text(j.get("description"))))
    return out


def fetch_himalayas(query):
    # Its search API takes the words itself, 20 jobs a page; three pages is plenty for one query.
    def pages():
        out = []
        for page in (1, 2, 3):
            d = jb.get_json("https://himalayas.app/jobs/api/search", {"q": query, "page": page})
            out += d.get("jobs", [])
            if len(d.get("jobs", [])) < 20:
                break
        return out
    out = []
    for j in _cached("himalayas_" + "".join(c if c.isalnum() else "_" for c in query.lower())[:40], pages):
        pay = f"{j['currency']} {j['minSalary']:,}-{j['maxSalary']:,} {j.get('salaryPeriod', '')}".strip() \
            if j.get("minSalary") and j.get("maxSalary") else ""
        posted = j.get("pubDate")
        out.append(_row(j["title"], j.get("companyName", ""), ", ".join(j.get("locationRestrictions") or []) or "Worldwide",
                        "Remote", jb.employment(j.get("employmentType")),
                        jb.day(posted * 1000) if isinstance(posted, (int, float)) else str(posted or "")[:10], pay,
                        ", ".join((j.get("parentCategories") or j.get("categories") or [])[:3]), "Himalayas",
                        j.get("applicationLink") or j.get("guid") or "", jb.to_text(j.get("description"))))
    return [r for r in out if r[11]]


# Arbeitnow (fetch_arbeitnow) is left out: it is almost all German jobs, so it only buried US results.
LEGAL = {"Remotive": fetch_remotive, "Remote OK": fetch_remoteok, "Himalayas": fetch_himalayas}


def search(query, location="", log=print, accept=None):
    """Rows from every legal source whose title matches every word of the query (or that accept() takes)."""
    rows = []
    for name, fetch in LEGAL.items():
        try:
            jobs = fetch(query)
        except Exception as e:
            log(f"  {name}: couldn't read it ({type(e).__name__})")
            continue
        hits = [r for r in jobs if (accept(r) if accept else jb.title_matches(r[0], query) and (
            jb.location_matches(r[2], r[3], location) or any(w in r[2].lower() for w in ANYWHERE)))]
        log(f"  {name}: {len(hits)} matching of {len(jobs)}")
        rows += hits
    return rows


MAX_AGE_DAYS = 7


def _fresh(posted, days=MAX_AGE_DAYS):
    # A job with no readable date stays; one posted more than `days` days ago goes.
    try:
        return (date.today() - datetime.strptime(posted[:10], "%Y-%m-%d").date()).days <= days
    except ValueError:
        return True


def _public_match(r, query, location, days=MAX_AGE_DAYS, remote_ok=True):
    """Broader than the title test: the words may be in the title or the field, or the phrase in the text.
    Remote jobs stay (unless remote_ok is off); other jobs must fit the location."""
    words = re.findall(r"\w+", query.lower())
    head = f"{r[0]} {r[8]}".lower()
    if words and not (all(w in head for w in words) or " ".join(words) in r[13].lower()):
        return False
    remote = "remote" in f"{r[2]} {r[3]}".lower() or any(w in r[2].lower() for w in ANYWHERE)
    return _fresh(r[5], days) and ((remote and remote_ok) or jb.location_matches(r[2], r[3], location))


KEY_FILE = Path(__file__).parent / "jsearch_key.txt"       # git-ignored; the free RapidAPI key for JSearch


def jsearch_key():
    import os
    try:
        return os.environ.get("JSEARCH_KEY") or KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def fetch_jsearch(query, location, days, key, log=print):
    """JSearch (RapidAPI) reads Google for Jobs, which gathers LinkedIn, Indeed, Glassdoor and company sites.

    This is the 'whole web' source, set to the United States (a blank location means all of the US). The free plan is small (about 200 searches a month), so an answer is kept
    CACHE_HOURS and each search asks for one page only. Every row names the site it came from.
    """
    when = "today" if days <= 1 else "3days" if days <= 3 else "week" if days <= 7 else "month"
    text = f"{query} in {location}" if location else query
    cache = "jsearch_us_" + "".join(c if c.isalnum() else "_" for c in f"{text} {when}".lower())[:60]

    def get():
        r = requests.get("https://jsearch.p.rapidapi.com/search",
                         params={"query": text, "page": 1, "num_pages": 1, "date_posted": when, "country": "us"},
                         headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}, timeout=30)
        if r.status_code in (401, 403):
            raise PermissionError("the JSearch key was refused (check jsearch_key.txt and that you subscribed to the free plan)")
        r.raise_for_status()
        return r.json().get("data") or []
    out = []
    for j in _cached(cache, get):
        place = ", ".join(x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x)
        pay = ""
        if j.get("job_min_salary") and j.get("job_max_salary"):
            pay = f"{j.get('job_salary_currency') or ''} {j['job_min_salary']:,.0f}-{j['job_max_salary']:,.0f}".strip()
        link = j.get("job_apply_link") or j.get("job_google_link") or ""
        if j.get("job_title") and link:
            out.append(_row(j["job_title"], j.get("employer_name", ""), place, "Remote" if j.get("job_is_remote") else "",
                            jb.employment(j.get("job_employment_type")), (j.get("job_posted_at_datetime_utc") or "")[:10],
                            pay, "", f"{j.get('job_publisher') or 'Web'} via JSearch", link, j.get("job_description") or ""))
    return out


ATS_SITES = ["job-boards.greenhouse.io", "boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com",
             "apply.workable.com", "jobs.smartrecruiters.com"]


def _web_hits(site, query, place):
    from ddgs import DDGS
    urls = []
    for q in (f'site:{site} "{query}" {place}', f"site:{site} {query} {place}"):
        try:
            urls += [x["href"] for x in DDGS().text(q.strip(), max_results=10, backend="bing")]
        except Exception:       # "No results" or a throttled engine: the other phrasing may still work
            pass
    return urls


def web_boards(query, location="", log=print):
    """Searches the web for employers that have this kind of job open, on the five career-site platforms.

    The search finds pages like jobs.lever.co/acme or boards.greenhouse.io/acme/jobs/123; only the company's
    id is taken from them, and the real job list is then read from the platform's own feed (exact and fresh).
    Returns [(platform, id, "")] for boards not yet on the company list; the answer is kept CACHE_HOURS.
    """
    try:
        import ddgs  # noqa: F401
    except ImportError:
        log("  Web search: needs 'pip install ddgs' (skipped).")
        return []
    place = re.split(r"[,;]", location)[0].strip()
    key = "web_" + "".join(c if c.isalnum() else "_" for c in f"{query} {place}".lower())[:60]

    def search_all():
        found = set()
        with ThreadPoolExecutor(3) as pool:
            for urls in pool.map(lambda site: _web_hits(site, query, place), ATS_SITES):
                for u in urls:
                    b = jb.board_from_url(u)
                    if b:
                        found.add(b)
        return sorted(found)
    boards = _cached(key, search_all)
    have = {(p, sl.lower()) for p, sl, _ in jb.load_companies()}
    new = [(p, sl, "") for p, sl in map(tuple, boards) if (p, sl.lower()) not in have]
    log(f"  Web search: {len(boards)} company boards found, {len(new)} not on your list")
    return new


def run(query, location="", log=print, stop=None, report=None, days=MAX_AGE_DAYS, remote_ok=True, web=True):
    """Public search: the open feeds first (they're quick), then every company on the career-site list.

    Only jobs from the last `days` days; remote jobs stay unless remote_ok is off; repeats are merged.
    With web=True it also searches the whole web for employers on the career-site platforms.
    One Excel file, one table, no login anywhere. Returns the saved path or None.
    """
    report = report or (lambda k, v: None)
    accept = lambda r: _public_match(r, query, location, days, remote_ok)
    report("status", "Reading the open job feeds")
    rows = search(query, location, log=log, accept=accept) if query else []
    if not query:
        log("  Open feeds: they need a search word, so only the company list is checked.")
    key = jsearch_key()
    if key and query:
        report("status", "Searching Google for Jobs (LinkedIn, Indeed, Glassdoor...)")
        try:
            jobs = fetch_jsearch(query, location, days, key)
            hits = [r for r in jobs if accept(r)]
            log(f"  JSearch: {len(hits)} matching of {len(jobs)}")
            rows += hits
        except Exception as e:
            log(f"  JSearch: couldn't read it ({type(e).__name__}: {e})")
    elif query:
        log("  JSearch: no key yet. Add a free one to jsearch_key.txt to also cover LinkedIn, Indeed and Glassdoor jobs.")
    companies = jb.load_companies()
    if web and query:
        report("status", "Searching the web for employers")
        companies += web_boards(query, location, log=log)
    return jb.run(query, location, log=log, stop=stop, report=report, seed=rows, kind="public", accept=accept,
                  merge=True, companies=companies)
