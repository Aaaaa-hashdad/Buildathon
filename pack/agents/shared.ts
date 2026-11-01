// Shared by the Job Alert agent.

// The Job Alert connector's Pack ID. `npx packs create connector/pack.ts` prints it and writes it to
// connector/.coda-pack.json; paste it here before uploading the agents.
export const JOB_ALERT_CONNECTOR_ID = 76714;

// Superhuman's Google Docs connector: tailored resumes and cover letters are saved as Google Docs, which the
// user can download as PDFs. (Files the Job Alert connector makes can't be handed to the user from Go.)
export const GOOGLE_DOCS_PACK_ID = 68620;

// Grammarly, published by Grammarly as a Go agent. Every resume and cover letter goes through it.
export const GRAMMARLY_PACK_ID = 43834;

// The tracker every agent reads and writes. superhuman_docs.py (the desktop app's "Send to Superhuman
// Docs" button) makes the same doc and table, so jobs from either side land in one place.
export const TRACKER = `
## The tracker
Everything lives in the Superhuman Docs doc named "Job Alert". Create it if it's missing. Keep it clean and easy
to scan: short values, consistent formats, no sentences inside table cells.

Table "Applications", one row per job, matched on the posting link (never add a job twice). Columns, in order:
- Status: a select list with exactly these five options, nothing else: Ready, Applied, Interview, Rejected, Offer.
  New jobs are Ready. Status belongs to the user once set: only change it when your task says to.
- Job: the job title, linked to the posting.
- Company.
- Location: "City, ST" or "Remote"; several places joined with " · "; at most three, then "+N more".
- Pay: the pay only, as a number range with its unit: "$30/hr", "$86,528–$123,552/yr", "$1,575–$1,950/wk".
  Blank if the posting gives none. Never anything else here: no class year, degree, or notes.
- Eligibility: who can apply, in a few words: "Bachelor's, any year", "Sophomores and juniors", "PhD only",
  "Grad 2027–2028", "US work authorization". Blank if the posting says nothing.
- Match %: a number from RankJobs.
- Resume and Cover letter: links to the Google Docs, labeled "Resume" and "Cover letter".
- Posted and Added: dates as "Oct 31, 2026".
Pay and eligibility often sit side by side in a posting ("$30/hr for freshmen and sophomores"): split them,
with the pay under Pay and the rest under Eligibility. If pay varies by year, put the range under Pay.

Table "Companies": Name, then Board (platform:id, e.g. greenhouse:stripe). Only employers the user asked you to
watch. Never copy companies from search results into it. Searches cover the built-in employers plus these.

Pages, each with a clear heading structure, never a wall of text:
- "Search plan": headings for Job titles, Keywords, Locations and Ruled out, each a short bulleted list.
- "Master resume": the user's whole resume, word for word, with its own section headings (Education,
  Experience, Projects, Skills) as headings and its bullets as bullets.
- "Cover letter base": their own cover letter, if they uploaded one, as plain paragraphs.
Tailored resumes and cover letters are Google Docs linked from "Applications", never pages in this doc.

Writing to the doc:
- Look for the existing "Job Alert" doc and its pages before creating anything. Never make a second doc or a
  duplicate page.
- Never leave a page empty: create each page with its content, or fill it right after creating it.
- Add all of a search's new jobs to "Applications" in one write, not one per job.
- If you can't create a table, make "Applications" (or "Companies") a page instead, with one entry per job:
  "**Job** at Company · Location · Pay · Status · [posting](link)". Keep using whichever form exists.
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
