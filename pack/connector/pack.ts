// Job Alert connector: open jobs from company career sites, for Superhuman Go agents and Docs.
// Public job-board APIs only, so no sign-in. Which platforms are on is set by ENABLED in boards.ts.

import * as sdk from "@superhuman/plugins-sdk";
import { DEFAULT_COMPANIES, DOMAINS, ENABLED, findCompany, getJob, Job, requireBoard, searchBoard } from "./boards";
import { readDocument } from "./documents";
import { findBestJobs } from "./best";
import { keywordMatch } from "./keywords";
import { markdownToPdf } from "./pdf";

export const pack = sdk.newPack();

pack.addNetworkDomain(...ENABLED.map(p => DOMAINS[p]));

const JobSchema = sdk.makeObjectSchema({
  properties: {
    id: { type: sdk.ValueType.String, description: "platform:company:job, e.g. greenhouse:stripe:12345. Pass it to GetJob." },
    title: { type: sdk.ValueType.String },
    company: { type: sdk.ValueType.String },
    location: { type: sdk.ValueType.String },
    workplace: { type: sdk.ValueType.String, description: "Remote, Hybrid or On-site, when the posting says." },
    type: { type: sdk.ValueType.String, description: "Full-time, Contract, Internship..." },
    posted: { type: sdk.ValueType.String, codaType: sdk.ValueHintType.Date },
    salary: { type: sdk.ValueType.String },
    department: { type: sdk.ValueType.String },
    platform: { type: sdk.ValueType.String },
    applyLink: { type: sdk.ValueType.String, codaType: sdk.ValueHintType.Url },
    jobUrl: { type: sdk.ValueType.String, codaType: sdk.ValueHintType.Url },
    description: { type: sdk.ValueType.String },
    board: { type: sdk.ValueType.String, description: "The company board, platform:id." },
  },
  displayProperty: "title",
  idProperty: "id",
  featuredProperties: ["company", "location", "posted", "jobUrl"],
  titleProperty: "title",
  linkProperty: "jobUrl",
  subtitleProperties: ["company", "location"],
  index: { properties: ["description"] },
});

const CompanySchema = sdk.makeObjectSchema({
  properties: {
    board: { type: sdk.ValueType.String, description: "platform:id, to pass to SearchJobs." },
    name: { type: sdk.ValueType.String, description: "The company name the board reports." },
    platform: { type: sdk.ValueType.String },
    slug: { type: sdk.ValueType.String },
    openJobs: { type: sdk.ValueType.Number },
    verified: { type: sdk.ValueType.Boolean, description: "True for a job link, or when the board's own name matches. False = check with the user." },
  },
  displayProperty: "board",
});

const companiesParam = sdk.makeParameter({
  type: sdk.ParameterType.StringArray,
  name: "companies",
  description: "Company boards as platform:id (greenhouse:stripe, lever:spotify) or careers links. FindCompany looks one up by name.",
});
const searchCompaniesParam = sdk.makeParameter({
  type: sdk.ParameterType.StringArray,
  name: "companies",
  description: "Extra company boards as platform:id (greenhouse:stripe) or careers links. About 55 well-known " +
    "employers in tech, aerospace, civil engineering, finance and health are always searched as well.",
  optional: true,
});
const queryParam = sdk.makeParameter({
  type: sdk.ParameterType.String,
  name: "query",
  description: "Words that must all be in the job title, e.g. 'civil engineer'. Blank = every job.",
  optional: true,
});
const locationParam = sdk.makeParameter({
  type: sdk.ParameterType.String,
  name: "location",
  description: "Places separated by commas, e.g. 'Houston, Remote'. Plain text match, so 'Texas' won't match 'Houston, TX'. Blank = anywhere.",
  optional: true,
});

// Full descriptions are one request each, so only this many come back with them; GetJob has the rest.
const MAX_DESCRIPTIONS = 25;

pack.addFormula({
  name: "SearchJobs",
  description: "Open jobs whose title has every search word, from each company's career site: a built-in list of " +
    "well-known employers plus any companies given.",
  parameters: [
    searchCompaniesParam,
    queryParam,
    locationParam,
    sdk.makeParameter({
      type: sdk.ParameterType.Boolean,
      name: "includeDescription",
      description: "Return full descriptions for up to 25 jobs. Off by default. Use GetJob for one job's full text.",
      optional: true,
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.Number,
      name: "limit",
      description: "Most jobs to return, 100 by default.",
      optional: true,
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.Boolean,
      name: "onlyTheseCompanies",
      description: "Search only the companies given, not the built-in employers too. For 'jobs at SpaceX'-type asks.",
      optional: true,
    }),
  ],
  resultType: sdk.ValueType.Array,
  items: JobSchema,
  execute: async function ([companies = [], query = "", location = "", includeDescription = false, limit = 100,
    onlyTheseCompanies = false], context) {
    // The built-in employers are always searched; the user's own companies are added to them.
    const boards = onlyTheseCompanies && companies.length ? companies
      : [...new Set([...companies, ...DEFAULT_COMPANIES])];
    // Titles first; descriptions only for the jobs that make the cut.
    const results = await Promise.allSettled(
      boards.map(async c => searchBoard(context, requireBoard(c), query, location, limit, false)));
    const perCompany = results.map(r => (r.status === "fulfilled" ? r.value : []));
    const failed = results.filter((r): r is PromiseRejectedResult => r.status === "rejected");
    if (failed.length === results.length) {
      throw failed.length === 1 ? failed[0].reason
        : new sdk.UserVisibleError("None of those company boards answered. Check the ids with FindCompany.");
    }
    // Taken in turns, so the limit doesn't fill up with the first company's jobs.
    const jobs: Job[] = [];
    for (let i = 0; jobs.length < limit && perCompany.some(list => i < list.length); i++) {
      perCompany.forEach(list => i < list.length && jobs.push(list[i]));
    }
    const picked = jobs.slice(0, limit);
    if (!includeDescription) {
      return picked.map(j => ({ ...j, description: j.description.slice(0, 300) }));
    }
    const full = await Promise.allSettled(picked.slice(0, MAX_DESCRIPTIONS).map(j => getJob(context, j.id)));
    return full.map((r, i) => (r.status === "fulfilled" ? r.value : picked[i])).concat(picked.slice(MAX_DESCRIPTIONS));
  },
});

pack.addFormula({
  name: "GetJob",
  description: "One job with its full description, by the id SearchJobs returned.",
  parameters: [
    sdk.makeParameter({
      type: sdk.ParameterType.String,
      name: "jobId",
      description: "platform:company:job, e.g. greenhouse:stripe:12345.",
    }),
  ],
  resultType: sdk.ValueType.Object,
  schema: JobSchema,
  execute: async function ([jobId], context) {
    return getJob(context, jobId);
  },
});

pack.addFormula({
  name: "FindCompany",
  description: "Finds a company's career-site board from its name or any job link. Boards guessed from a name are unverified.",
  parameters: [
    sdk.makeParameter({
      type: sdk.ParameterType.String,
      name: "nameOrLink",
      description: "A company name ('Hugging Face') or a link to one of its job posts.",
    }),
  ],
  resultType: sdk.ValueType.Array,
  items: CompanySchema,
  execute: async function ([nameOrLink], context) {
    return findCompany(context, nameOrLink);
  },
});

pack.addFormula({
  name: "ReadDocument",
  description: "The text of an uploaded resume or cover letter (Word .docx or text), to tailor from. PDFs aren't supported: paste their text.",
  parameters: [
    sdk.makeParameter({
      type: sdk.ParameterType.File,
      name: "file",
      description: "A file uploaded to Superhuman: attached in chat or added to a doc.",
    }),
  ],
  resultType: sdk.ValueType.Object,
  schema: sdk.makeObjectSchema({
    properties: {
      text: { type: sdk.ValueType.String },
      format: { type: sdk.ValueType.String, description: "Word or Text." },
    },
    displayProperty: "format",
  }),
  execute: async function ([file], context) {
    return readDocument(context, file);
  },
});

pack.addFormula({
  name: "FindBestJobs",
  description: "The whole search in one call: every title against every employer, read in full, scored against the " +
    "resume by keywords, with pay and eligibility pulled out. Returns the best matches first.",
  parameters: [
    sdk.makeParameter({
      type: sdk.ParameterType.StringArray,
      name: "titles",
      description: "Short title searches, one to three words each: 'software engineer', 'software intern', 'data'.",
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.String,
      name: "resume",
      description: "The resume's full text, to score each job against.",
    }),
    locationParam,
    sdk.makeParameter({
      type: sdk.ParameterType.StringArray,
      name: "exclude",
      description: "Title words to leave out, e.g. senior, staff, principal, manager, director for a student.",
      optional: true,
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.Number,
      name: "count",
      description: "How many to return, 20 by default.",
      optional: true,
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.StringArray,
      name: "companies",
      description: "Extra company boards to search besides the built-in employers.",
      optional: true,
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.String,
      name: "country",
      description: "'US' keeps only US places and remote jobs that don't name another country. Blank = anywhere.",
      optional: true,
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.Boolean,
      name: "onlyTheseCompanies",
      description: "Search only the companies given, not the built-in employers too.",
      optional: true,
    }),
  ],
  resultType: sdk.ValueType.Array,
  items: sdk.makeObjectSchema({
    properties: {
      id: { type: sdk.ValueType.String },
      title: { type: sdk.ValueType.String },
      company: { type: sdk.ValueType.String },
      location: { type: sdk.ValueType.String },
      posted: { type: sdk.ValueType.String },
      jobUrl: { type: sdk.ValueType.String, codaType: sdk.ValueHintType.Url },
      matchPercent: { type: sdk.ValueType.Number },
      matched: { type: sdk.ValueType.Array, items: { type: sdk.ValueType.String } },
      missing: { type: sdk.ValueType.Array, items: { type: sdk.ValueType.String } },
      pay: { type: sdk.ValueType.String, description: "The pay the posting states, or blank." },
      eligibility: { type: sdk.ValueType.String, description: "The posting's sentence on degree, year or authorization." },
    },
    displayProperty: "title",
  }),
  execute: async function ([titles, resume, location = "", exclude = [], count = 20, companies = [], country = "",
    onlyTheseCompanies = false], context) {
    return findBestJobs(context, {
      titles, resume, location, exclude, count, country,
      companies: onlyTheseCompanies && companies.length ? companies : [...new Set([...companies, ...DEFAULT_COMPANIES])],
    });
  },
});

pack.addFormula({
  name: "RankJobs",
  description: "Scores jobs against a resume by keywords: the skills and tools each job names, and which ones the " +
    "resume shows. Returns the jobs best match first, with matched and missing keywords.",
  parameters: [
    sdk.makeParameter({
      type: sdk.ParameterType.StringArray,
      name: "jobIds",
      description: "Job ids from SearchJobs (greenhouse:stripe:12345). Up to 40.",
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.String,
      name: "resume",
      description: "The resume's full text.",
    }),
  ],
  resultType: sdk.ValueType.Array,
  items: sdk.makeObjectSchema({
    properties: {
      id: { type: sdk.ValueType.String },
      title: { type: sdk.ValueType.String },
      company: { type: sdk.ValueType.String },
      location: { type: sdk.ValueType.String },
      jobUrl: { type: sdk.ValueType.String, codaType: sdk.ValueHintType.Url },
      matchPercent: { type: sdk.ValueType.Number, description: "Share of the job's keywords the resume shows." },
      matched: { type: sdk.ValueType.Array, items: { type: sdk.ValueType.String } },
      missing: { type: sdk.ValueType.Array, items: { type: sdk.ValueType.String } },
    },
    displayProperty: "title",
  }),
  execute: async function ([jobIds, resume], context) {
    const jobs = await Promise.allSettled(jobIds.slice(0, 40).map(id => getJob(context, id)));
    return jobs
      .filter((r): r is PromiseFulfilledResult<Job> => r.status === "fulfilled")
      .map(({ value: j }) => ({
        id: j.id, title: j.title, company: j.company, location: j.location, jobUrl: j.jobUrl,
        ...keywordMatch(resume, `${j.title}\n${j.description}`, j.company),
      }))
      .sort((a, b) => b.matchPercent - a.matchPercent || a.missing.length - b.missing.length);
  },
});

pack.addFormula({
  name: "MakePdf",
  description: "Turns a resume or cover letter written in simple Markdown into a clean PDF, and returns a link to it.",
  parameters: [
    sdk.makeParameter({
      type: sdk.ParameterType.String,
      name: "markdown",
      description: "The document. '# Full Name' then the contact line under it; '## Section' headings; '### Role' lines; " +
        "'- ' bullets; **bold**; blank lines between paragraphs. No title like 'Resume - Company - Job' inside it.",
    }),
    sdk.makeParameter({
      type: sdk.ParameterType.String,
      name: "fileName",
      description: "The download name, e.g. 'Jordan Lee - Resume - MongoDB.pdf'.",
    }),
  ],
  resultType: sdk.ValueType.Object,
  schema: sdk.makeObjectSchema({
    properties: {
      file: { type: sdk.ValueType.String, codaType: sdk.ValueHintType.Attachment,
        description: "The PDF. The link expires; saving it into a doc keeps it." },
      fileName: { type: sdk.ValueType.String },
      pages: { type: sdk.ValueType.Number },
    },
    displayProperty: "fileName",
  }),
  execute: async function ([markdown, fileName], context) {
    const name = /\.pdf$/i.test(fileName) ? fileName : `${fileName}.pdf`;
    const { bytes, pages } = await markdownToPdf(markdown, name.replace(/\.pdf$/i, ""));
    const file = await context.temporaryBlobStorage.storeBlob(Buffer.from(bytes), "application/pdf",
      { downloadFilename: name, expiryMs: 7 * 24 * 60 * 60 * 1000 });
    return { file, fileName: name, pages };
  },
});

pack.addSyncTable({
  name: "Jobs",
  description: "Open jobs from company career sites that match a title and location.",
  identityName: "Job",
  schema: JobSchema,
  formula: {
    name: "SyncJobs",
    description: "Syncs matching open jobs, one company per step.",
    parameters: [companiesParam, queryParam, locationParam],
    execute: async function ([companies, query = "", location = ""], context) {
      // One company per run keeps each step well inside the time limit.
      const index = Number(context.sync.continuation?.index ?? 0);
      if (index >= companies.length) {
        return { result: [] };
      }
      let jobs: Job[] = [];
      try {
        jobs = await searchBoard(context, requireBoard(companies[index]), query, location, 500);
      } catch (e) {
        // A company that moved off its platform shouldn't stop the others from syncing.
        console.log(`Skipped ${companies[index]}: ${e}`);
      }
      const next = index + 1;
      return { result: jobs, continuation: next < companies.length ? { index: next } : undefined };
    },
  },
});
