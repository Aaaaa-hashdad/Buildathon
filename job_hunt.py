"""One search from a resume: several job titles across the career-site list and the open sources,
merged, de-duplicated and ranked by how many of the resume's skills each posting mentions.

The AI reads the resume and picks the titles and skills (see job_hunt_guide in mcp_server.py);
this module does the legwork. No LinkedIn here, so nothing touches the user's account.
"""

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import requests

import job_boards as jb
import linkedin_bot as bot
import open_sources

MAX_TITLES = 8
MAX_ROWS = 300
EXTRA_COLUMNS = ["Match", "Matched skills"]


def _company_jobs(company, title, location, stop):
    platform, slug, name = company
    try:
        jobs = jb.FETCHERS[platform](slug, name, title, location, stop)
    except (jb.NotFound, requests.RequestException, ValueError, KeyError):
        return []
    return [r for r in jobs if jb.title_matches(r[0], title) and jb.location_matches(r[2], r[3], location)]


def _skills_in(text, skills):
    found = []
    for s in skills:
        if re.search(r"(?<![\w+#])" + re.escape(s.lower()) + r"(?![\w+#])", text):
            found.append(s)
    return found


def _score(r, skills):
    """0-100: 50 for matching a wanted title, up to 50 for resume skills mentioned, a little for fresh posts."""
    found = _skills_in(f"{r[0]} {r[13]}".lower(), skills)
    pts = 50 + min(len(found), 10) * 5
    try:
        age = (date.today() - datetime.strptime(r[5][:10], "%Y-%m-%d").date()).days
        pts += 5 if age <= 7 else 2 if age <= 30 else 0
    except ValueError:
        pass
    return min(pts, 100), found


def find(titles, skills=(), location="", use_open_sources=True, log=print, stop=None):
    """Returns (saved path or None, ranked rows). Each ranked row is [Match, Matched skills, *job columns]."""
    stop = stop or threading.Event()
    titles = [t.strip() for t in titles if t.strip()][:MAX_TITLES]
    skills = [s.strip() for s in skills if s.strip()]
    if not titles:
        raise ValueError("Give at least one job title.")
    rows = []

    companies = jb.load_companies()
    log(f"Career sites: {len(companies)} companies x {len(titles)} titles")
    with ThreadPoolExecutor(jb.COMPANY_WORKERS) as pool:
        for hits in pool.map(lambda ct: _company_jobs(ct[0], ct[1], location, stop),
                             [(c, t) for t in titles for c in companies]):
            rows += hits
    if use_open_sources:
        for t in titles:
            log(f"Open sources for '{t}':")
            rows += open_sources.search(t, location, log=log)

    # One row per company + title: a posting repeated for ten cities becomes one row listing the cities,
    # so a single employer can't fill the whole ranking.
    seen_urls, groups = set(), {}
    for r in rows:
        url = (r[12] or "").split("?")[0]
        if url and url in seen_urls:
            continue
        seen_urls.add(url)
        k = (re.sub(r"\W+", " ", r[0].lower()).strip(), r[1].lower())
        if k in groups:
            if r[2] and r[2] not in groups[k][1]:
                groups[k][1].append(r[2])
        else:
            groups[k] = (r, [r[2]] if r[2] else [])
    unique = []
    for r, places in groups.values():
        if len(places) > 1:
            r = list(r)
            r[2] = f"{places[0]} (+{len(places) - 1} more: {'; '.join(places[1:6])}{'...' if len(places) > 6 else ''})"
        unique.append(r)

    ranked = []
    for r in unique:
        score, found = _score(r, skills)
        ranked.append([score, ", ".join(found)] + r)
    ranked.sort(key=lambda x: x[7] or "", reverse=True)     # newest first...
    ranked.sort(key=lambda x: x[0], reverse=True)           # ...then best match first (stable)
    ranked = ranked[:MAX_ROWS]
    if not ranked:
        log("No matching jobs. Try broader titles, a wider location, or add more companies.")
        return None, []
    path = bot.save("matches", " ".join(titles), ranked, columns=EXTRA_COLUMNS + bot.COLUMNS["jobs"])
    log(f"Saved {len(ranked)} ranked jobs -> {path}")
    return path, ranked
