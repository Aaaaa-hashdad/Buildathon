// Job Alert connector: open jobs from company career sites, for Superhuman Go agents and Docs.
// Public job-board APIs only, so no sign-in. Which platforms are on is set by ENABLED in boards.ts.

import * as sdk from "@superhuman/plugins-sdk";
import { DEFAULT_COMPANIES, DOMAINS, ENABLED, findCompany, getJob, Job, requireBoard, searchBoard } from "./boards";
import { readDocument } from "./documents";

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
  description: "Company boards as platform:id (greenhouse:stripe) or careers links. Leave empty to search about 55 " +
    "well-known employers in tech, aerospace, civil engineering, finance and health.",
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
  description: "Open jobs whose title has every search word, from each company's career site. With no companies, " +
    "searches a built-in list of well-known employers.",
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
  ],
  resultType: sdk.ValueType.Array,
  items: JobSchema,
  execute: async function ([companies = [], query = "", location = "", includeDescription = false, limit = 100], context) {
    const boards = companies.length ? companies : DEFAULT_COMPANIES;
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
