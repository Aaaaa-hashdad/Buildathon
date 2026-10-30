# LinkedIn Data Bot – Project Notes

A desktop app that collects public LinkedIn data (people, companies, jobs) into Excel, with an
MCP server so Claude can read the results and tailor resumes on a normal subscription (no API key).

Built 2026-10-29. This file is the handoff: read it before continuing work.

---

## How to run

The app is called **Job Alert** (window title, teal theme, bell logo). It opens on **Where to search**:
**Company career sites** (the default; no Chrome or login) or **LinkedIn**. The Chrome step, the
People/Companies/Jobs choice, pages and speed only appear for LinkedIn, and the company list only for career
sites. Step numbers come from a CSS counter, so they stay 1-2-3-4 either way. The last source picked is
remembered (localStorage). The folder is still `linkedin-bot`, because `.mcp.json` points to it.

LinkedIn steps:
1. Double-click **`run_app.bat`**. It opens the Electron window. Pick **LinkedIn**.
2. Click **Open Chrome**. It opens the bot's own Chrome window (port 9222, profile in `chrome-profile\`).
   Log into LinkedIn there the first time; it stays logged in.
   - Don't use your normal Chrome. Chrome blocks remote control on the default profile.
3. Pick People / Companies / Jobs, type a search, an optional location, pages and speed, then click **Start**.
4. Results are saved to `output\<kind>_<search>_<date>.xlsx`.

Command line (no window):
```
python linkedin_bot.py jobs "civil engineer" --location "Houston" --pages 1 --speed medium
python linkedin_bot.py people "marketing manager" --location "Mumbai"
```
Flags: `--pages 1-10`, `--speed low|medium|high`, `--grab-apply-links` (jobs only).

Company career sites (no LinkedIn, no Chrome, no login):
```
python job_boards.py search "civil engineer" --location "Houston, Remote"
python job_boards.py add "Stripe"                        # name: tries all 5 platforms
python job_boards.py add https://jobs.lever.co/spotify   # or any job link from the company
python job_boards.py import                              # companies found in LinkedIn apply links
python job_boards.py list
```
In the app: Where to search → **Company career sites**. Results: `output\boards_<search>_<date>.xlsx`.

---

## Files

| File | Role |
|---|---|
| `run_app.bat` | Launches the Electron app |
| `electron\main.js` | Electron main: spawns `backend.py`, relays events, runs the BOT ACTIVE badge window |
| `electron\index.html`, `renderer.js`, `style.css` | Main window UI (light/dark) |
| `electron\badge.html` | Floating always-on-top badge (status, count, Stop) |
| `electron\preload.js` | Exposes `window.bot` (send, onEvent, openPath, openUrl) |
| `backend.py` | JSON-lines bridge on stdin/stdout between Electron and the bot |
| `linkedin_bot.py` | **The bot**: connect over CDP, search, read, save Excel |
| `job_boards.py` | **Career-site source**: Greenhouse, Lever, Ashby, Workable, SmartRecruiters public APIs |
| `companies.txt` | Companies `job_boards.py` checks: `platform:id  Optional Name`, one per line |
| `mcp_server.py` | MCP server `linkedin-bot` (FastMCP, stdio) |
| `app.py` | Old Tkinter window, kept as a backup (`python app.py`) |
| `start_chrome.bat` | Opens the bot's Chrome without the app |
| `profile\` | Put the master resume here (.docx / .pdf / .md / .txt) |
| `resumes\` | Tailored resumes and cover letters get saved here |
| `output\` | Excel results |
| `chrome-profile\` | The bot's Chrome data and LinkedIn login. Keep it private and never share it. |

**Connect AI assistants** (app card, `mcp_setup.py`): connects the MCP server on any PC, with the paths worked out at
runtime (`sys.executable` + this folder).
- **Claude Desktop:** writes `mcpServers.linkedin-bot` into every Claude Desktop config it finds, keeping the
  other settings and saving a `.json.bak`. The Windows Store app's config is
  `%LOCALAPPDATA%\Claude-Data\<pkg>\claude_desktop_config.json`; a standard install uses
  `%APPDATA%\Claude\claude_desktop_config.json`. Claude Desktop must be fully quit and reopened afterwards.
- **Claude Code:** runs `claude mcp add --scope user linkedin-bot -- <python> <mcp_server.py>`, so it works in
  every folder without the approval prompt.
- **Other apps:** "Copy settings" puts the JSON on the clipboard. Superhuman Go's "Add custom MCP" probably
  wants a URL, not a command. The server only does stdio for now, so that's not supported yet.
- "Needs update" = connected, but to an old path (folder moved): click Connect again.

**Superhuman Docs tracker** (`superhuman_docs.py`, "Send to Superhuman Docs" button): uses the free Docs API with
a personal token in `superhuman_token.txt`. Git ignores that file; never share it. The first send creates a
"Job Alert" doc and tries to make an Applications table from an HTML table in a page, because the API can't create
tables. If that fails, the user makes the table by hand. Rows are matched on Job URL. Status, Match % and Resume
are only set on new rows. **Not tested yet: waiting on the user's token.**

The old way MCP is registered: **`.mcp.json` in the parent project folder** (`Alert System\.mcp.json`) with an
**absolute path** to `mcp_server.py`. **If you move this folder, update that path** (or create a new
`.mcp.json` in the new project root).

---

## What works (tested on real LinkedIn)

- **People search:** Name, Headline, Location, Connection, Profile URL. 10/10 and 15/15 rows clean.
- **Jobs search:** clicks every job card and reads the details panel. Last run got 25/25 jobs:
  Title, Location, Job URL, Description 25/25; Company, Industry, Company size 24/25;
  Apply type (Easy Apply / Company site) 25/25; real company apply link for 10 of 17 Company-site jobs.
- **Live view:** a "Bot reading" pill and an outline on the current card in Chrome, the BOT ACTIVE badge,
  rows streaming into the app table.
- **Speeds:** low ~3s per action, medium ~1s, high ~0.2s (`SPEEDS` in `linkedin_bot.py`).
- **Safety:** random pauses, max 10 pages per run, max 100 jobs per run, stops and beeps on login or checkpoint
  URLs, read-only (never clicks Connect, Message or Follow; Apply only with `--grab-apply-links`).
- **Electron app:** window, badge, Chrome-connected light, backend shuts down on close. A real People
  search was run through it (Autodesk License and Compliance, 15 rows).
- **MCP:** all 6 tools called through a real MCP client and confirmed connected in Claude Code.
- **Career sites (`job_boards.py`, built 2026-10-29):** all 5 platforms tested on live APIs. Rows, links and
  full descriptions checked on each. Langan (Greenhouse, found through `import`) gave 194 civil engineer jobs,
  6 in Houston. Backend protocol and the 3 new MCP tools also tested (called directly, not yet from Claude Code).
  App panel checked by screenshot.

## Career sites: how it works and what to know

- Each API lists **one company at a time** and has no keyword search, so `companies.txt` is the search scope.
  A job is kept when **every search word is in the title** ("engineer" also matches "Engineering"), and
  location is a plain text match against the job's location and workplace. "Texas" won't match "Houston, TX".
- Same 14 columns as LinkedIn jobs. **Apply type = the platform name**, Applicants and Company size are blank,
  Industry holds the department when the platform has no industry. Max 500 rows per run.
- **Speed and Stop (fixed 2026-10-29):** 6 companies are checked at once (`COMPANY_WORKERS`), and SmartRecruiters
  pages and job details 6 at a time. A full search of the 6 test companies takes about 4s (it was 16s).
  Stop is checked every 0.2s and works mid-company.

## App speed: what slowed it down (fixed, don't bring these back)

- The Chrome check used an HTTP request to `localhost:9222`. On Windows a refused local connection takes about
  2s, it ran every 3s, and it blocked the backend's command loop, so **Stop and every other button waited
  behind it**. Now it's a 0.3s socket check on `127.0.0.1`, polled by the page only while LinkedIn is picked.
- Rows used to carry full descriptions (up to 32k chars) to both windows. Now `backend.report` cuts each cell
  to 300 chars (Excel still gets everything), and the badge only gets status lines and an empty row ping.
- The table drew and scrolled once per row. Now rows are queued and drawn once per frame (`flushRows`).
- The window is shown on `ready-to-show`, so there's no white flash at start.
- APIs: Greenhouse `boards-api.greenhouse.io/v1/boards/<id>/jobs?content=true` (content is double-escaped HTML) ·
  Lever `api.lever.co/v0/postings/<id>?mode=json` (falls back to `api.eu.lever.co`) ·
  Ashby `api.ashbyhq.com/posting-api/job-board/<id>?includeCompensation=true` (has salary) ·
  Workable `apply.workable.com/api/v1/widget/accounts/<id>?details=true` ·
  SmartRecruiters `api.smartrecruiters.com/v1/companies/<id>/postings` (no descriptions in the list, so each
  match is fetched separately; its `q=` search is loose, so titles are still filtered on our side).
- **Workable and SmartRecruiters answer unknown IDs with an empty board instead of a 404**, so adding by name
  only counts a platform that has at least one open job.
- Adding by name guesses IDs (`stripe`, `hugging-face`...). Long IDs like Langan's
  `langanengineeringandenvironmentalservicesllc` can't be guessed, so paste a job link instead.
- **Import companies from LinkedIn results** (button, `job_boards.py import`) does two things:
  1. Adds companies whose apply link points at one of the 5 platforms. This is exact.
  2. Takes every other company name from `output\jobs_*.xlsx` and guesses its ID on each platform, with
     suffixes like Inc/LLC/PC/Company removed. Greenhouse, Workable and SmartRecruiters report the company's
     name, and its first word must match. **Lever and Ashby report no name**, so one-word names are skipped
     there (Ashby's `hatch` turned out to be a software startup, not the engineering firm) and multi-word
     matches are logged as "unverified". Looked-up names go in `company_lookups.json`, so the next import only
     checks new names. Delete an entry to have it checked again.
  - **LinkedIn view, Go row: "Add these companies to career sites"** (LinkedIn jobs only). It turns on after a
    jobs run saves and imports just that run's file (`boards_import` with `file`).
  - First real run (2026-10-29): 23 names, 30s. Found BGE, Inc. on Greenhouse (117 jobs). The other ~21 civil
    and health firms use Workday, iCIMS etc.
- **Civil engineering fit:** of the 27 apply links in the LinkedIn results, only Langan was on these 5 platforms.
  The rest were iCIMS, Workday, Paylocity, UKG and Oracle. Links on those systems are refused and the message
  names the system.
- The example companies in `companies.txt` (Stripe, Spotify, OpenAI, Hugging Face, Bosch) are one per platform
  from testing. Delete the ones you don't want.
- The sandboxed shell in Claude Code has no internet. Run live API tests outside the sandbox.

## Not verified yet

- Jobs columns **Workplace, Type, Posted, Applicants, Salary**: the parser was fixed for `•` vs `·`
  separators and checked offline only. Confirm on the next jobs run.
- `--grab-apply-links` (clicks Apply, reads the popup tab URL, closes it): never run.
- **Companies** search: never run.

---

## MCP server `linkedin-bot` – tools

| Tool | Notes |
|---|---|
| `list_results()` | Excel files, newest first |
| `read_results(file, offset, limit, include_description)` | Rows with a `row` index; descriptions omitted by default |
| `get_job(file, row)` | One row with the full description |
| `get_master_resume()` | Newest resume in `profile\` |
| `save_tailored_resume(company, job_title, resume_markdown, cover_letter_markdown)` | Markdown to .docx (`#`, `##`, `###`, `- `, `**bold**`) |
| `run_search(kind, query, location, pages, speed)` | Uses the user's LinkedIn account. **Always ask the user first.** |
| `search_job_boards(query, location)` | Career sites in `companies.txt`. No account involved, so no need to ask |
| `list_companies()` | What `search_job_boards` checks |
| `add_company(name_or_url)` | Adds a company by name or job link |

Paths are guarded: `read_results` and `get_job` only open files inside `output\`.

---

## LinkedIn page details (they change often; check here first when something breaks)

- **People and companies** search pages: results are found by link pattern (`/in/`, `/company/`).
  The card is "the biggest ancestor of the link that contains no other result's link" (`CARD_JS`).
  Results aren't wrapped in `<li>` anymore.
- **LinkedIn switches accounts between two jobs pages** (it says it's "gradually retiring classic job search").
  The bot handles both. After page 1 loads it checks the address: still on `/jobs/search/` means classic.
  - **Classic:** cards are `li[data-occludable-job-id="<id>"]`. Cards also contain `/jobs/view/<id>` links, so
    `panelLink` skips links inside cards. `?location=` works, but "X in Y" is read as keywords and LinkedIn adds
    the home country ("in India"), so with a location page 1 is loaded a second time the classic way.
    "Share" / "Show more options" sit next to the company name (`HEADER_SKIP`). Industry and size are separated
    by runs of non-breaking spaces (`SEPARATORS`). Apply is a button with no link, so company apply links need
    `--grab-apply-links`.
  - Tested 2026-10-29 on classic: 5/5 jobs read, with title, Houston location, posted date, applicants, type and
    description. The company and industry/size fixes came afterwards and were checked offline only.
- **Jobs (new layout)** redirect to the new `/jobs/search-results/` page:
  - Class names are scrambled, and there are no `data-job-id` or `data-view-name` attributes.
  - Cards: `[componentkey^="job-card-component-ref-<jobId>"]` (duplicated, so de-dupe).
  - The details panel has the title link `a[href*="/jobs/view/<id>"]`, plus `h2` "About the job" and
    "About the company". "About the company" only renders after the panel is scrolled down.
  - The panel can be read before "About the job" loads. `JOB_PANEL_JS` returns null until it's there.
  - The `?location=` parameter is **ignored**, so the location goes into the keywords: `"civil engineer in Houston"`.
  - External apply links are wrapped as `linkedin.com/safety/go?url=<real>`, which `unwrap()` decodes.
- **People results as "LinkedIn Member"** (seen 2026-10-29, same day as the bot-check frames): no name and no
  `/in/` link, so 0 saved. LinkedIn hides people outside your network, much more so after the monthly
  search limit or when it's limiting the account. **Don't try to get around it.** The bot now counts these
  cards (`HIDDEN_MEMBERS_JS`) and says so instead of a silent 0.
- People and companies location is just added to the keywords ("Research College Station"), so LinkedIn
  matches the words anywhere and returns results from everywhere (India, here). A real location filter needs
  LinkedIn's geo IDs (`geoUrn`). Not built.
- Login or checkpoint detection: the URL contains `/checkpoint/`, `/authwall`, `/login` or `/uas/`.
- LinkedIn logged the bot's session out once after many test runs close together.
  **Keep test runs few and small.**
- 2026-10-29 18:4x: a LinkedIn run in the app ended with nothing saved. The bot's LinkedIn tab was loading
  bot-detection frames (`li.protechts.net ... uc=scraping`) and a Google reCAPTCHA enterprise frame. An empty
  page 1 now beeps and says to check Chrome, instead of a bare "No more results". **Never automate CAPTCHAs.**
  The user solves them. Give LinkedIn a rest and use career sites.

Known: `BOT_SCREENSHOT_KIND=boards` didn't switch back to career sites once LinkedIn had been remembered.
This is a dev-only issue and hasn't been looked into.

Dev tip: `BOT_SCREENSHOT=<path.png>` makes the Electron app save its own window and badge to PNGs and
quit (`BOT_SCREENSHOT_KIND=boards` picks career sites, `people`/`companies`/`jobs` picks LinkedIn and that type). The capture can come before the backend's first
answers arrive, so the Chrome light and company count may still show their defaults. Use it instead of desktop screenshots, which caught other private windows twice.

---

## Rules we agreed on

- Public data only. No posting, spamming or mass messaging.
- The bot never types passwords; the user logs in themself.
- Resume tailoring: **only reword and reorder real experience. Never invent skills or experience.**
- Autofill (future): fill the fields, **stop before Submit**, and let the human make the final click.
  Never fill salary, visa or work authorization, demographic questions, legal confirmations, or passwords.
- Prefer company-site applications (Greenhouse, Lever first). LinkedIn Easy Apply automation is a common
  reason accounts get banned, so only prepare answers for it and let the user paste them in.

---

## Next steps (in order)

1. **Resume tailor:** the user drops their master resume in `profile\`. Then for each chosen job:
   pull out keywords, match % and missing keywords, write a tailored 1-page resume (plus an optional cover letter),
   and save it with `save_tailored_resume`. Runs through Claude via MCP, no API key.
2. **Profile file:** standard application answers stored once (contact, links, education, work history).
3. **Autofill** for Greenhouse and Lever: highlight filled fields (green = profile data, yellow = AI-written),
   stop before Submit.
4. **Workday and others**, one at a time. Workday matters most for civil engineering. It also has a public
   jobs endpoint (`<company>.wdN.myworkdayjobs.com/wday/cxs/<company>/<site>/jobs`) that could become a 6th
   source in `job_boards.py`.
5. **Tracker:** found → tailored → ready → applied, with dates and follow-ups.
6. Cancelled earlier, maybe later: a first-launch disclaimer ("the account is your responsibility"), a red risk bar,
   and an "are you sure?" popup for High speed or grab-apply-links.
