// Shared by the Job Alert agent.

// The Job Alert connector's Pack ID. `npx packs create connector/pack.ts` prints it and writes it to
// connector/.coda-pack.json; paste it here before uploading the agents.
export const JOB_ALERT_CONNECTOR_ID = 76714;

// The tracker every agent reads and writes. superhuman_docs.py (the desktop app's "Send to Superhuman
// Docs" button) makes the same doc and table, so jobs from either side land in one place.
export const TRACKER = `
## The tracker
Everything lives in the Superhuman Docs doc named "Job Alert". Create it if it's missing.
- Table "Applications", one row per job, matched on Job URL (never add a job twice):
  Job, Company, Location, Posted, Salary, Source, Apply link, Job URL, Status, Match %, Resume, Added.
  Status is one of: Found, Tailored, Ready, Applied, Interview, Offer, Rejected, Withdrawn.
  Status, Match % and Resume belong to the user once set: only change them when your task says to.
- Table "Companies": Board (platform:id, e.g. greenhouse:stripe), Name. These are the career sites searched.
- Pages "Master resume" (the only source of truth about the user) and "Cover letter base" (their own cover
  letter, if they uploaded one).
- Page "Search plan": the titles, keywords and location the user is looking for.

Writing to the doc:
- Find the existing "Job Alert" doc and its pages before creating anything. Never make a second doc or a
  duplicate page.
- Never leave a page empty. Create each page with its content in the same step, or write the content right
  after creating it. Then read the page back: if it's empty, write it again.
- If you can't create a table, make "Applications" (or "Companies") a page instead, with one entry per job:
  "**Job** at Company · Location · Status · [posting](Job URL)". Keep using whichever form exists.
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
