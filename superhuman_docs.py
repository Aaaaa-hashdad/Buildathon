"""Sends jobs to a Superhuman Docs tracker (the Coda API underneath).

One doc, "Job Alert", holds an Applications table: a row per job, matched on Job URL, so sending a
job again updates it instead of adding a duplicate. Your Status, Match % and Resume cells are only
filled in for new rows and never overwritten. Later, tailored resumes become pages in the same doc,
where Superhuman Go can polish them.

Setup (once):
    1. Superhuman Docs > your account settings > API > generate a token.
    2. Paste it into superhuman_token.txt in this folder (git ignores that file). Never share it.
    3. python superhuman_docs.py setup        # creates the doc and its Applications table

Usage:
    python superhuman_docs.py send output\\boards_civil_engineer_2026-10-29_1845.xlsx
    python superhuman_docs.py status
"""

import argparse
import html
import json
import time
from datetime import date
from pathlib import Path

import requests
from openpyxl import load_workbook

HERE = Path(__file__).parent
TOKEN_FILE = HERE / "superhuman_token.txt"
CONFIG_FILE = HERE / "superhuman.json"         # doc and table IDs, written by setup
# The API reference names docs.superhuman.com; older docs say coda.io. Whichever answers is kept.
BASES = ("https://docs.superhuman.com/apis/v1", "https://coda.io/apis/v1")
DOC_TITLE = "Job Alert"
TABLE_NAME = "Applications"

# Applications table columns, and where each comes from in a results row (Excel column name).
# None = yours to fill in: only set on new rows, never overwritten.
TRACKER = [
    ("Job", "Title"), ("Company", "Company"), ("Location", "Location"), ("Posted", "Posted"),
    ("Salary", "Salary"), ("Source", "Apply type"), ("Apply link", "Apply link"), ("Job URL", "Job URL"),
    ("Status", None), ("Match %", None), ("Resume", None), ("Added", None),
]
KEY = "Job URL"
ROWS_PER_WRITE = 100
WRITE_PAUSE = 0.7           # the API allows 10 writes per 6 seconds


class NotConnected(Exception):
    pass


# ---------- API ----------

def token():
    try:
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        t = ""
    if not t:
        raise NotConnected(f"No Superhuman Docs token. Create one in Superhuman Docs (account settings > API) "
                           f"and paste it into {TOKEN_FILE.name} in the bot folder.")
    return t


def config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=1), encoding="utf-8")


def api(method, path, base=None, **kw):
    base = base or config().get("base") or BASES[0]
    for attempt in range(5):
        r = requests.request(method, base + path, headers={"Authorization": f"Bearer {token()}"}, timeout=30, **kw)
        if r.status_code == 429:            # over the rate limit: back off and retry, as the API asks
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code == 401:
            raise NotConnected("Superhuman Docs rejected the token. Make a new one and paste it into "
                               f"{TOKEN_FILE.name}.")
        r.raise_for_status()
        return r.json() if r.content else {}
    r.raise_for_status()


def find_base():
    """Which API host accepts the token. Returns (base, whoami)."""
    errors = []
    for base in BASES:
        try:
            return base, api("GET", "/whoami", base=base)
        except NotConnected:
            raise
        except requests.RequestException as e:
            errors.append(f"{base}: {e}")
    raise NotConnected("Couldn't reach Superhuman Docs: " + "; ".join(errors))


def wait_for(request_id, timeout=30):
    """Writes are applied in the background; this waits until one is done."""
    if not request_id:
        return
    end = time.time() + timeout
    while time.time() < end:
        try:
            if api("GET", f"/mutationStatus/{request_id}").get("completed"):
                return
        except requests.HTTPError:
            pass
        time.sleep(1)


def paged(path, **params):
    items, params = [], dict(params, limit=params.get("limit", 100))
    while True:
        data = api("GET", path, params=params)
        items += data.get("items", [])
        if not data.get("nextPageToken"):
            return items
        params["pageToken"] = data["nextPageToken"]


# ---------- setup ----------

def tracker_table_html():
    head = "".join(f"<th>{html.escape(name)}</th>" for name, _ in TRACKER)
    return f"<table><thead><tr>{head}</tr></thead><tbody><tr>{'<td></td>' * len(TRACKER)}</tr></tbody></table>"


def find_table(doc_id):
    for t in paged(f"/docs/{doc_id}/tables", tableTypes="table"):
        if t.get("name") == TABLE_NAME:
            return t
    return None


def setup(log=print):
    """Connects, creates the Job Alert doc if needed, and finds (or tries to create) the Applications table."""
    base, me = find_base()
    cfg = config()
    cfg["base"] = base
    save_config(cfg)
    log(f"Connected to Superhuman Docs as {me.get('name', '?')}.")

    if not cfg.get("doc_id"):
        doc = api("POST", "/docs", json={"title": DOC_TITLE})
        cfg.update(doc_id=doc["id"], doc_link=doc.get("browserLink", ""))
        save_config(cfg)
        log(f"Created the '{DOC_TITLE}' doc: {cfg['doc_link'] or cfg['doc_id']}")
        time.sleep(2)
    doc_id = cfg["doc_id"]

    table = find_table(doc_id)
    if not table:
        # The API can't create tables. A page with an HTML table may become one, so try that first.
        res = api("POST", f"/docs/{doc_id}/pages", json={
            "name": TABLE_NAME,
            "pageContent": {"type": "canvas", "canvasContent": {"format": "html", "content": tracker_table_html()}},
        })
        wait_for(res.get("requestId"))
        for _ in range(5):
            table = find_table(doc_id) or next(iter(paged(f"/docs/{doc_id}/tables", tableTypes="table")), None)
            if table:
                break
            time.sleep(2)
    if not table:
        log("Superhuman Docs didn't turn the page into a table, so it needs one by hand (2 minutes):")
        log(f"  open {cfg.get('doc_link') or 'the Job Alert doc'}, add a table named '{TABLE_NAME}' with columns: "
            + ", ".join(name for name, _ in TRACKER) + ". Then run setup again.")
        return False

    cfg["table_id"] = table["id"]
    save_config(cfg)
    columns = {c["name"] for c in paged(f"/docs/{doc_id}/tables/{table['id']}/columns")}
    missing = [name for name, _ in TRACKER if name not in columns]
    if missing:
        log(f"The '{table.get('name')}' table is missing columns: {', '.join(missing)}. Add them, then run setup again.")
        return False
    log(f"Ready: '{table.get('name')}' table with all {len(TRACKER)} columns.")
    return True


# ---------- sending jobs ----------

def read_results(path):
    rows = list(load_workbook(path, read_only=True).active.iter_rows(values_only=True))
    header = [str(h) for h in rows[0]]
    return [dict(zip(header, ("" if v is None else v for v in r))) for r in rows[1:]]


def send(path, log=print, only_rows=None):
    """Adds or updates the jobs from one results file. only_rows: row numbers (0-based) to send."""
    cfg = config()
    if not cfg.get("table_id"):
        raise NotConnected("Superhuman Docs isn't set up yet. Click 'Connect Superhuman Docs' first.")
    doc_id, table_id = cfg["doc_id"], cfg["table_id"]
    jobs = read_results(path)
    if only_rows is not None:
        jobs = [jobs[i] for i in only_rows if 0 <= i < len(jobs)]
    jobs = [j for j in jobs if j.get(KEY)]
    if not jobs:
        log("No jobs with a Job URL in that file (people and company searches can't be tracked).")
        return 0

    col_ids = {c["name"]: c["id"] for c in paged(f"/docs/{doc_id}/tables/{table_id}/columns")}
    existing = {str(r["values"].get(col_ids[KEY], "")).strip()
                for r in paged(f"/docs/{doc_id}/tables/{table_id}/rows", valueFormat="simple")}

    def cells(job, new):
        out = [{"column": col_ids[name], "value": str(job.get(src, ""))[:2000]}
               for name, src in TRACKER if src]
        if new:
            out += [{"column": col_ids["Status"], "value": "Found"},
                    {"column": col_ids["Added"], "value": date.today().isoformat()}]
        return out

    new_rows = [{"cells": cells(j, True)} for j in jobs if str(j[KEY]).strip() not in existing]
    old_rows = [{"cells": cells(j, False)} for j in jobs if str(j[KEY]).strip() in existing]
    request = None
    for batch_rows in (new_rows, old_rows):
        for i in range(0, len(batch_rows), ROWS_PER_WRITE):
            res = api("POST", f"/docs/{doc_id}/tables/{table_id}/rows",
                      json={"rows": batch_rows[i:i + ROWS_PER_WRITE], "keyColumns": [col_ids[KEY]]})
            request = res.get("requestId") or request
            time.sleep(WRITE_PAUSE)
    wait_for(request)
    log(f"Superhuman Docs: {len(new_rows)} new jobs added, {len(old_rows)} already there updated. "
        f"{cfg.get('doc_link', '')}".rstrip())
    return len(new_rows) + len(old_rows)


def status():
    cfg = config()
    connected = TOKEN_FILE.exists() and TOKEN_FILE.read_text(encoding="utf-8").strip() != ""
    return {"token": connected, "ready": bool(cfg.get("table_id")), "doc_link": cfg.get("doc_link", "")}


def main():
    ap = argparse.ArgumentParser(description="Send jobs to your Superhuman Docs tracker.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="connect and create the Job Alert doc + Applications table")
    s = sub.add_parser("send", help="add or update the jobs from a results file")
    s.add_argument("file")
    sub.add_parser("status")
    args = ap.parse_args()
    try:
        if args.cmd == "setup":
            setup()
        elif args.cmd == "send":
            send(args.file)
        else:
            print(status())
    except NotConnected as e:
        print(e)


if __name__ == "__main__":
    main()
