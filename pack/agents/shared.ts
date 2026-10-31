// Shared by the Job Alert agent.

// The Job Alert connector's Pack ID. `npx packs create connector/pack.ts` prints it and writes it to
// connector/.coda-pack.json; paste it here before uploading the agents.
export const JOB_ALERT_CONNECTOR_ID = 76714;

// Superhuman's Google Docs connector: tailored resumes and cover letters are saved as Google Docs, which the
// user can download as PDFs. (Files the Job Alert connector makes can't be handed to the user from Go.)
export const GOOGLE_DOCS_PACK_ID = 68620;

// The tracker every agent reads and writes. superhuman_docs.py (the desktop app's "Send to Superhuman
// Docs" button) makes the same doc and table, so jobs from either side land in one place.
export const TRACKER = `
## The tracker
Everything lives on ONE page: the first page of the Superhuman Docs doc named "Job Alert". No other pages.
The page has these sections, in this order:
- "## Search plan": the titles, keywords and location the user is looking for.
- "## Applications": a table, one row per job, matched on Job URL (never add a job twice), with columns
  Job, Company, Location, Posted, Source, Job URL, Status, Match %, Resume, Added.
  Status is one of: Found, Tailored, Ready, Applied, Interview, Offer, Rejected, Withdrawn.
  Status, Match % and Resume belong to the user once set: only change them when your task says to.
- "## Companies": the career-site boards searched (platform:id, e.g. greenhouse:stripe), if the user picked any.
- "## Master resume": the user's whole resume, word for word. The only source of truth about them.
- "## Cover letter base": their own cover letter, if they uploaded one.
Tailored resumes and cover letters are Google Docs linked from "Applications", never pages.

Writing to the doc:
- The user approves every change you make to their doc, one tool call at a time, so keep it to the fewest calls:
  first time, create the doc, then write the whole page (title "Job Alert", every section filled) in one
  update. That's two approvals in total. After that, one update per task: gather everything first (all the new
  jobs, the status changes, the resume link), then write the page once at the end.
- Before creating anything, look for the existing "Job Alert" doc. Never make a second doc or another page.
- Never write a section, then write again to fill it. One write with everything in it.
- Each time you change the doc, say what you added or changed in one line.
`;

// Rules agreed for the whole project (PROJECT_NOTES.md, "Rules we agreed on").
export const RULES = `
## Rules
- Public job data only. Never send anything to recruiters, hiring managers or anyone else on the user's
  behalf: write drafts the user reviews and sends. The only email you send is to the user themselves.
- Never invent skills, experience, titles, dates, numbers or credentials. Only reword and reorder what the
  master resume says. If a job needs something the resume doesn't have, say it's missing; don't paper over it.
- Never submit an application. The user makes the final click.
- Never fill in or guess salary expectations, visa or work authorization, demographic questions, legal
  confirmations, or passwords. Leave those to the user.
- Prefer applying on the company's own site (Greenhouse, Lever first) over LinkedIn Easy Apply.
- Always link the job posting when you mention a job.
`;

/** The connector as an agent tool, limited to `formulas` when given. Empty until the ID is filled in. */
export function jobAlertConnector(formulas?: string[]) {
  if (!JOB_ALERT_CONNECTOR_ID) {
    return [];
  }
  return [{ packId: JOB_ALERT_CONNECTOR_ID, formulas: formulas?.map(formulaName => ({ formulaName })) }];
}
