# Job Alert for Superhuman Go

Job Alert as a Superhuman Go agent. It runs on Superhuman's servers, so nobody has to install or run anything
on their computer.

## For people you send it to

1. Install **Job Alert** in Superhuman Go (from the Store link, or from the Packs shared with you).
2. Connect your mail and Superhuman Docs when Go asks.
3. Click **Find jobs for me**. The agent asks what you're looking for and for your resume, and sets up a
   "Job Alert" doc to keep everything in.

The job search itself needs no sign-in: it uses the career sites' public job APIs. To get a morning digest of
new jobs and automatic status updates from application emails, turn on the agent's routines in its settings.
They install paused.

## What's here

| Folder | What it is |
|---|---|
| `connector/` | **Job Alert connector.** Open jobs from company career sites through their public APIs (a port of `job_boards.py`), and reading uploaded resumes and cover letters (PDF, Word, text). Only Greenhouse is on for now: see "Turning on the other platforms". |
| `agents/job-alert/` | **Job Alert agent.** Finds jobs (career sites + web search), tailors a resume and cover letter per job from the master resume, and keeps each job's Status current from email. |
| `agents/shared.ts` | The connector's Pack ID, the tracker layout, and the rules the agent follows. |

The agent's routines:
- **Morning job sweep and Monday follow-ups**, weekdays at 8 AM in the user's time zone: adds new jobs to the
  tracker and emails the user a digest. On Mondays it also drafts follow-ups for applications that have gone
  quiet, and never sends them.
- **Update from application emails**: when an email arrives from an applicant-tracking system, or its subject
  looks like an application email, it sets the job's Status (Applied, Interview, Rejected, Offer).
- **Uploads**: attach a resume, cover letter or job description in chat (PDF, Word or text). The resume becomes
  the Master resume, the cover letter becomes the base that tailored letters start from.
- **While writing**: offers to tailor when the user is writing a cover letter or a reply to a recruiter.

Everything is kept in each user's own Superhuman Docs doc, **Job Alert**: an Applications table, a Companies
table, a Search plan page and a Master resume page. `superhuman_docs.py` (the desktop app's "Send to Superhuman
Docs") fills the same Applications table.

**Left out on purpose:** the LinkedIn bot. It drives a logged-in Chrome on your PC and risks the account.
The agent uses web search for LinkedIn and Indeed postings instead.

## Connector formulas

| Formula | Does |
|---|---|
| `SearchJobs(companies, query, location, includeDescription, limit)` | Open jobs whose title has every word of `query`. Companies are `platform:id` (`greenhouse:stripe`) or careers links. |
| `GetJob(jobId)` | One job with its full description, by the id SearchJobs gave it (`greenhouse:stripe:8144196`). |
| `FindCompany(nameOrLink)` | A company's board from its name (guessed, unverified) or any job link (exact). |
| `ReadDocument(file)` | The text of an uploaded resume, cover letter or job description: PDF, Word .docx or text. Files must be uploaded to Superhuman (attached in chat or added to a doc). |
| `Jobs` sync table | The same search as a table in Superhuman Docs, one company per sync step. |

## Publishing (once, by you)

```
cd pack
npm install
npx packs register                    # paste an API token from Superhuman Docs > account settings > API
```

1. **Create and release the connector.** It only calls `greenhouse.io` for now, so no approval is needed:
   ```
   npx packs create connector/pack.ts --name "Job Alert connector" --description "Open jobs from company career sites"
   npx packs upload connector/pack.ts --notes "First version"
   npx packs release connector/pack.ts --notes "First version"
   ```
   Paste the Pack ID from `connector/.coda-pack.json` into `JOB_ALERT_CONNECTOR_ID` in `agents/shared.ts`.
2. **Create the agent, and let it use the connector:**
   ```
   npx packs create agents/job-alert/pack.ts --name "Job Alert"
   npx packs upload agents/job-alert/pack.ts --notes "First version"
   npx packs settings update connector/pack.ts --allowedAgents <agent id from agents/job-alert/.coda-pack.json>
   npx packs agent install agents/job-alert/pack.ts       # installs it for you, to try it first
   npx packs agent chat agents/job-alert/pack.ts "what can you do?"
   ```
3. **Share it.** Packs are private until you share them. Share both packs with your team, or submit both for
   review to list them in the Superhuman Store, so anyone can install them from a link:
   ```
   npx packs settings update agents/job-alert/pack.ts --tagline "Finds jobs, tailors your resume, tracks applications" --supportEmail <you>
   ```

After changing the agent's tools or triggers, upload again and run `npx packs agent install <pack.ts> --reinstall`.

## Turning on the other platforms

Lever, Ashby, Workable and SmartRecruiters are written and tested, but a Pack with more than one domain needs
Superhuman's approval (a few business days):
https://docs.superhuman.com/form/Pack-Network-Domains-Request_ddvuAhFq3IZ
Ask for "Multiple network domains" with `greenhouse.io, lever.co, ashbyhq.com, workable.com, smartrecruiters.com`,
signed in with the account you publish from. Once approved, set `ENABLED` in `connector/boards.ts` to all five
platforms, then upload and release the connector again.

## Try the connector locally

```
npx packs execute connector/pack.ts SearchJobs greenhouse:stripe,greenhouse:woolpert engineer "Remote"
npx packs execute connector/pack.ts GetJob greenhouse:stripe:8144196
npx packs execute connector/pack.ts FindCompany "Hugging Face"
npx packs execute connector/pack.ts Jobs greenhouse:langanengineeringandenvironmentalservicesllc "civil engineer" Houston
npm run validate                      # both packs
```

Agents are an early-access part of the SDK (`newAgent` is marked internal), so their API may still change,
and the public docs don't yet describe how agents are shared. Check that step when you get there.
