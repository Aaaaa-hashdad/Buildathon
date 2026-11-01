// Jobs straight from company career sites, through their public job-board APIs.
// A port of job_boards.py: same five platforms, same matching rules, no login anywhere.

import * as sdk from "@superhuman/plugins-sdk";

export const PLATFORMS: Record<string, string> = {
  greenhouse: "Greenhouse",
  lever: "Lever",
  ashby: "Ashby",
  workable: "Workable",
  smartrecruiters: "SmartRecruiters",
};

// Each platform's API domain. A Pack with more than one domain needs Superhuman's approval, so only the
// platforms in ENABLED are declared and searched. Add the rest back once the approval is granted.
export const DOMAINS: Record<string, string> = {
  greenhouse: "greenhouse.io",
  lever: "lever.co",
  ashby: "ashbyhq.com",
  workable: "workable.com",
  smartrecruiters: "smartrecruiters.com",
};
export const ENABLED = ["greenhouse"];

const SR_MAX_POSTINGS = 1000;   // SmartRecruiters pages 100 at a time; big employers have thousands
const SR_MAX_DETAILS = 50;      // SmartRecruiters needs one request per job for its description
const CACHE_SECS = 15 * 60;

export interface Board {
  platform: string;
  slug: string;
}

export interface Job {
  id: string;
  title: string;
  company: string;
  location: string;
  workplace: string;
  type: string;
  posted: string;
  salary: string;
  department: string;
  platform: string;
  applyLink: string;
  jobUrl: string;
  description: string;
  board: string;
}

export class NotFound extends Error {}

async function getJson(context: sdk.ExecutionContext, url: string, params: Record<string, string | number> = {}) {
  const query = Object.entries(params).map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");
  try {
    const response = await context.fetcher.fetch({
      method: "GET",
      url: query ? `${url}?${query}` : url,
      cacheTtlSecs: CACHE_SECS,
    });
    return response.body;
  } catch (e) {
    if (e instanceof sdk.StatusCodeError && e.statusCode === 404) {
      throw new NotFound(url);
    }
    throw e;
  }
}

// ---------- text helpers ----------

const ENTITIES: Record<string, string> = {
  amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", ndash: "–", mdash: "—", rsquo: "’",
  lsquo: "‘", rdquo: "”", ldquo: "“", hellip: "…", bull: "•", middot: "·", copy: "©", reg: "®", trade: "™",
};

function unescape(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (whole, code: string) => {
    if (code[0] === "#") {
      const n = code[1].toLowerCase() === "x" ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return Number.isFinite(n) ? String.fromCodePoint(n) : whole;
    }
    return ENTITIES[code.toLowerCase()] ?? whole;
  });
}

/** HTML job description -> plain text with line breaks and '- ' bullets. */
export function toText(markup: string | undefined): string {
  if (!markup) {
    return "";
  }
  let s = markup.includes("&lt;") ? unescape(markup) : markup;     // Greenhouse double-escapes
  s = s.replace(/<li[^>]*>/gi, "\n- ");
  s = s.replace(/<br\s*\/?>|<\/(p|div|li|ul|ol|h[1-6]|tr)>/gi, "\n");
  s = unescape(s.replace(/<[^>]+>/g, ""));
  s = s.replace(/[ \t ]+/g, " ");
  s = s.split("\n").map(l => l.trim()).join("\n").replace(/\n\s*\n+/g, "\n\n");
  return s.replace(/\n\n- /g, "\n- ").trim();
}

/** ISO string or epoch milliseconds -> YYYY-MM-DD. */
function day(value: unknown): string {
  if (!value) {
    return "";
  }
  if (typeof value === "number") {
    return new Date(value).toISOString().slice(0, 10);
  }
  return String(value).slice(0, 10);
}

function workplaceOf(...hints: unknown[]): string {
  const text = hints.filter(Boolean).join(" ").toLowerCase().replace(/_/g, "-");
  for (const [word, label] of [["hybrid", "Hybrid"], ["remote", "Remote"], ["on-site", "On-site"], ["onsite", "On-site"]]) {
    if (text.includes(word)) {
      return label;
    }
  }
  return "";
}

function employment(value: string | undefined): string {
  const v = (value || "").replace(/[_\- ]/g, "").toLowerCase();
  const known: Record<string, string> = {
    fulltime: "Full-time", parttime: "Part-time", contract: "Contract", contractor: "Contract",
    temporary: "Temporary", intern: "Internship", internship: "Internship", permanent: "Full-time",
  };
  return known[v] ?? (value || "");
}

function pretty(slug: string): string {
  return slug.replace(/[-_.]+/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

function job(board: Board, jobId: string | number, fields: Omit<Job, "id" | "platform" | "board">): Job {
  return {
    ...fields,
    title: (fields.title || "").trim(),
    id: `${board.platform}:${board.slug}:${jobId}`,
    platform: PLATFORMS[board.platform],
    board: `${board.platform}:${board.slug}`,
    description: (fields.description || "").replace(/[ \t ]+/g, " "),
  };
}

// ---------- matching ----------

/**
 * Every word of the search is a word in the title, allowing the usual endings: 'engineer' matches
 * 'Engineering' and 'intern' matches 'Internship', but 'intern' doesn't match 'International'.
 */
export function titleMatches(title: string, query: string): boolean {
  const t = title.toLowerCase();
  return ((query || "").toLowerCase().match(/\w+/g) || []).every(w =>
    new RegExp(`\\b${w}(s|es|ing|ings|ship|ships|er|ers|ed)?\\b`).test(t));
}

/** wanted can list several places: 'Houston, Remote'. Blank = anywhere. */
export function locationMatches(location: string, workplace: string, wanted: string): boolean {
  const places = (wanted || "").split(",").map(w => w.trim().toLowerCase()).filter(Boolean);
  if (!places.length) {
    return true;
  }
  const text = `${location} ${workplace}`.toLowerCase();
  return places.some(p => text.includes(p));
}

// ---------- one fetcher per platform ----------
// Each returns every open job for the company; matching happens afterwards in searchBoard().

function greenhouseJob(board: Board, j: any): Job {
  const loc = j.location?.name || "";
  return job(board, j.id, {
    title: j.title,
    company: j.company_name || pretty(board.slug),
    location: loc,
    workplace: workplaceOf(loc),
    type: "",
    posted: day(j.first_published || j.updated_at),
    salary: "",
    department: (j.departments || []).map((d: any) => d.name).join(", "),
    applyLink: `https://job-boards.greenhouse.io/${board.slug}/jobs/${j.id}`,
    jobUrl: j.absolute_url,
    description: toText(j.content),
  });
}

// Without content the list is a fraction of the size, which is what lets one search cover dozens of companies.
async function fetchGreenhouse(context: sdk.ExecutionContext, board: Board, withContent = true): Promise<Job[]> {
  const data = await getJson(context, `https://boards-api.greenhouse.io/v1/boards/${board.slug}/jobs`,
    withContent ? { content: "true" } : {});
  return data.jobs.map((j: any) => greenhouseJob(board, j));
}

async function fetchLever(context: sdk.ExecutionContext, board: Board): Promise<Job[]> {
  let data: any[];
  try {
    data = await getJson(context, `https://api.lever.co/v0/postings/${board.slug}`, { mode: "json" });
  } catch (e) {
    if (!(e instanceof NotFound)) {
      throw e;
    }
    data = await getJson(context, `https://api.eu.lever.co/v0/postings/${board.slug}`, { mode: "json" });   // EU-hosted
  }
  return data.map((j: any) => {
    const c = j.categories || {};
    const locs: string[] = c.allLocations || [c.location || ""];
    const sal = j.salaryRange || {};
    const salary = sal.min || sal.max
      ? `${sal.currency || ""} ${(sal.min || 0).toLocaleString("en-US")}-${(sal.max || 0).toLocaleString("en-US")} ${sal.interval || ""}`.trim()
      : "";
    const parts = [j.descriptionPlain || ""];
    for (const section of j.lists || []) {
      parts.push(`${section.text || ""}\n${toText(section.content)}`);
    }
    parts.push(j.additionalPlain || "");
    return job(board, j.id, {
      title: j.text,
      company: pretty(board.slug),
      location: locs.filter(Boolean).join("; "),
      workplace: workplaceOf(j.workplaceType, c.location),
      type: employment(c.commitment),
      posted: day(j.createdAt),
      salary,
      department: c.department || c.team || "",
      applyLink: j.applyUrl || "",
      jobUrl: j.hostedUrl,
      description: parts.map(p => p.trim()).filter(Boolean).join("\n\n"),
    });
  });
}

async function fetchAshby(context: sdk.ExecutionContext, board: Board): Promise<Job[]> {
  const data = await getJson(context, `https://api.ashbyhq.com/posting-api/job-board/${board.slug}`,
    { includeCompensation: "true" });
  return data.jobs.filter((j: any) => j.isListed !== false).map((j: any) => {
    const locs: string[] = [j.location || "", ...(j.secondaryLocations || []).map((s: any) => s.location || "")];
    const comp = j.compensation || {};
    return job(board, j.id, {
      title: j.title,
      company: pretty(board.slug),
      location: locs.filter(Boolean).join("; "),
      workplace: workplaceOf(j.workplaceType, j.isRemote ? "remote" : "", ...locs),
      type: employment(j.employmentType),
      posted: day(j.publishedAt),
      salary: comp.scrapeableCompensationSalarySummary || comp.compensationTierSummary || "",
      department: j.department || "",
      applyLink: j.applyUrl || "",
      jobUrl: j.jobUrl,
      description: j.descriptionPlain || toText(j.descriptionHtml),
    });
  });
}

async function fetchWorkable(context: sdk.ExecutionContext, board: Board): Promise<Job[]> {
  const data = await getJson(context, `https://apply.workable.com/api/v1/widget/accounts/${board.slug}`, { details: "true" });
  return data.jobs.map((j: any) => {
    let locs: string[] = (j.locations || []).filter((l: any) => !l.hidden)
      .map((l: any) => [l.city, l.region, l.country].filter(Boolean).join(", "));
    if (!locs.length) {
      locs = [[j.city, j.state, j.country].filter(Boolean).join(", ")];
    }
    return job(board, j.shortcode || j.id, {
      title: j.title,
      company: data.name || pretty(board.slug),
      location: locs.filter(Boolean).join("; "),
      workplace: j.telecommuting ? "Remote" : "",
      type: employment(j.employment_type),
      posted: day(j.published_on || j.created_at),
      salary: "",
      department: j.industry || j.department || "",
      applyLink: j.application_url || "",
      jobUrl: j.url,
      description: toText(j.description),
    });
  });
}

function srLocation(p: any): string {
  const loc = p.location || {};
  return loc.fullLocation || [loc.city, loc.region, loc.country].filter(Boolean).join(", ");
}

// The detail endpoint repeats the list fields, so a job can be built from it alone (getJob).
async function srDetail(context: sdk.ExecutionContext, board: Board, id: string, listed: any = {}): Promise<Job> {
  const d = await getJson(context, `https://api.smartrecruiters.com/v1/companies/${board.slug}/postings/${id}`);
  const p = { ...listed, ...d };
  const sections: any[] = Object.values(d.jobAd?.sections || {});
  const loc = p.location || {};
  return job(board, id, {
    title: p.name || "",
    company: p.company?.name || pretty(board.slug),
    location: srLocation(p),
    workplace: loc.remote ? "Remote" : loc.hybrid ? "Hybrid" : "",
    type: p.typeOfEmployment?.label || "",
    posted: day(p.releasedDate),
    salary: "",
    department: p.industry?.label || "",
    applyLink: d.applyUrl || "",
    jobUrl: d.postingUrl || "",
    description: sections.filter(s => s.text).map(s => `${s.title || ""}\n${toText(s.text)}`).join("\n\n"),
  });
}

// The list has no descriptions, so it's narrowed with SmartRecruiters' own (loose) search and each
// title-matching job's details are fetched separately. Returns already-matched jobs.
async function searchSmartRecruiters(context: sdk.ExecutionContext, board: Board, query: string,
  location: string, limit: number): Promise<Job[]> {
  const base = `https://api.smartrecruiters.com/v1/companies/${board.slug}/postings`;
  const params: Record<string, string | number> = { limit: 100, offset: 0 };
  if (query) {
    params.q = query;
  }

  async function fetchAll(extra: Record<string, string>): Promise<any[]> {
    const first = await getJson(context, base, { ...params, ...extra });
    const offsets = [];
    for (let o = 100; o < Math.min(first.totalFound, SR_MAX_POSTINGS); o += 100) {
      offsets.push(o);
    }
    const pages = await Promise.all(offsets.map(o => getJson(context, base, { ...params, offset: o, ...extra })));
    return [first, ...pages].flatMap(page => page.content);
  }

  // Big employers have thousands of jobs, so with a location the filtering happens on their side:
  // as a city ("Plymouth"), else as a region ("MI").
  const places = location.split(",").map(w => w.trim()).filter(Boolean);
  let postings: any[] = [];
  if (!places.length || places.some(p => p.toLowerCase() === "remote")) {
    postings = await fetchAll({});
  } else {
    const ids = new Set();
    for (const place of places) {
      let found = await fetchAll({ city: place });
      if (!found.length) {
        found = await fetchAll({ region: place });
      }
      for (const p of found) {
        if (!ids.has(p.id)) {
          ids.add(p.id);
          postings.push(p);
        }
      }
    }
  }
  const matches = postings
    .filter(p => titleMatches(p.name, query) && locationMatches(srLocation(p), "", location))
    .slice(0, Math.min(limit, SR_MAX_DETAILS));
  return Promise.all(matches.map(p => srDetail(context, board, p.id, p)));
}

const FETCHERS: Record<string, (context: sdk.ExecutionContext, board: Board, withContent?: boolean) => Promise<Job[]>> = {
  greenhouse: fetchGreenhouse,
  lever: fetchLever,
  ashby: fetchAshby,
  workable: fetchWorkable,
};

/** Open jobs at one company whose title and location match. */
export async function searchBoard(context: sdk.ExecutionContext, board: Board, query = "", location = "",
  limit = 100, withContent = true): Promise<Job[]> {
  if (board.platform === "smartrecruiters") {
    return searchSmartRecruiters(context, board, query, location, limit);
  }
  const jobs = await FETCHERS[board.platform](context, board, withContent);
  return jobs.filter(j => titleMatches(j.title, query) && locationMatches(j.location, j.workplace, location))
    .slice(0, limit);
}

/** One job by the id searchBoard gave it ("greenhouse:stripe:12345"). */
export async function getJob(context: sdk.ExecutionContext, id: string): Promise<Job> {
  const [platform, slug, ...rest] = id.split(":");
  const jobId = rest.join(":");
  const board = { platform: (platform || "").toLowerCase(), slug };
  if (!PLATFORMS[board.platform] || !slug || !jobId) {
    throw new sdk.UserVisibleError(`"${id}" isn't a job id. It looks like greenhouse:stripe:12345.`);
  }
  requireEnabled(board.platform);
  if (board.platform === "smartrecruiters") {
    return srDetail(context, board, jobId);
  }
  if (board.platform === "greenhouse") {
    try {
      return greenhouseJob(board, await getJson(context,
        `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs/${jobId}`));
    } catch (e) {
      if (!(e instanceof NotFound)) {
        throw e;
      }
      throw new sdk.UserVisibleError(`That job (${id}) isn't listed anymore. It may have been filled or taken down.`);
    }
  }
  const found = (await FETCHERS[board.platform](context, board)).find(j => j.id === id);
  if (!found) {
    throw new sdk.UserVisibleError(`That job (${id}) isn't listed anymore. It may have been filled or taken down.`);
  }
  return found;
}

// ---------- companies ----------

// Employers searched when the user hasn't picked any: all on Greenhouse, checked open on 2026-10-31.
export const DEFAULT_COMPANIES = [
  // Software and internet
  "stripe", "airbnb", "figma", "discord", "reddit", "pinterest", "lyft", "instacart", "gitlab", "cloudflare",
  "datadog", "mongodb", "elastic", "twilio", "asana", "squarespace", "duolingo", "dropbox", "okta", "zscaler",
  "samsara", "toast", "databricks", "anthropic", "scaleai", "roblox", "epicgames", "riotgames", "nextdoor",
  // Aerospace, defense, robotics and hardware
  "spacex", "andurilindustries", "relativity", "rocketlab", "waymo", "nuro", "formlabs",
  // Civil and environmental engineering
  "woolpert", "bgeinc", "langanengineeringandenvironmentalservicesllc",
  // Finance
  "robinhood", "coinbase", "brex", "chime", "affirm", "sofi", "block", "gusto", "carta", "jumptrading",
  "janestreet", "point72",
  // Health and consumer
  "oscar", "flatironhealth", "glossier", "sweetgreen",
].map(slug => `greenhouse:${slug}`);

// Careers links that reveal the platform and the company's ID on it.
const BOARD_URLS: Array<[string, RegExp]> = [
  ["greenhouse", /(?:job-)?boards(?:\.eu)?\.greenhouse\.io\/(?:embed\/job_(?:app|board)\?for=)?([\w-]+)/i],
  ["greenhouse", /boards-api\.greenhouse\.io\/v1\/boards\/([\w-]+)/i],
  ["lever", /jobs(?:\.eu)?\.lever\.co\/([\w.-]+)/i],
  ["ashby", /jobs\.ashbyhq\.com\/([\w.%-]+)/i],
  ["workable", /apply\.workable\.com\/(?!j\/|api\/)([\w-]+)/i],
  ["workable", /\/\/(?!apply\.|www\.)([\w-]+)\.workable\.com/i],
  ["smartrecruiters", /(?:jobs|careers)\.smartrecruiters\.com\/([\w-]+)/i],
];
const NOT_SLUGS = new Set(["embed", "jobs", "j", "api", "v1", "oneclick-ui"]);

/** 'https://jobs.lever.co/spotify/123' or 'lever:spotify' -> {platform: 'lever', slug: 'spotify'}. */
export function parseBoard(text: string): Board | undefined {
  const s = (text || "").trim();
  const direct = s.match(/^(\w+):([\w.%-]+)$/);
  if (direct && PLATFORMS[direct[1].toLowerCase()]) {
    return { platform: direct[1].toLowerCase(), slug: direct[2] };
  }
  for (const [platform, pattern] of BOARD_URLS) {
    const m = s.match(pattern);
    if (m && !NOT_SLUGS.has(m[1].toLowerCase())) {
      return { platform, slug: m[1] };
    }
  }
  return undefined;
}

export function requireBoard(text: string): Board {
  const board = parseBoard(text);
  if (!board) {
    throw new sdk.UserVisibleError(`"${text}" isn't a company board. Use platform:id (greenhouse:stripe) or a careers ` +
      `link from ${ENABLED.map(p => PLATFORMS[p]).join(", ")}. FindCompany can look one up by name.`);
  }
  requireEnabled(board.platform);
  return board;
}

function requireEnabled(platform: string) {
  if (!ENABLED.includes(platform)) {
    throw new sdk.UserVisibleError(`${PLATFORMS[platform]} isn't connected yet; only ` +
      `${ENABLED.map(p => PLATFORMS[p]).join(", ")} is. Use web search for this company's jobs.`);
  }
}

/** How many jobs the company has open on that platform, or 0 if it isn't there. */
async function openJobs(context: sdk.ExecutionContext, board: Board): Promise<number> {
  const { platform, slug } = board;
  try {
    if (platform === "greenhouse") {
      return (await getJson(context, `https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`)).jobs.length;
    }
    if (platform === "lever") {
      return (await getJson(context, `https://api.lever.co/v0/postings/${slug}`, { mode: "json" })).length;
    }
    if (platform === "ashby") {
      return (await getJson(context, `https://api.ashbyhq.com/posting-api/job-board/${slug}`)).jobs.length;
    }
    if (platform === "workable") {
      // Unknown IDs return an empty board instead of a 404, so zero counts as not found.
      return (await getJson(context, `https://apply.workable.com/api/v1/widget/accounts/${slug}`)).jobs.length;
    }
    return (await getJson(context, `https://api.smartrecruiters.com/v1/companies/${slug}/postings`,
      { limit: 1 })).totalFound || 0;
  } catch (e) {
    return 0;
  }
}

const COMPANY_SUFFIXES = new Set(["inc", "llc", "pllc", "pc", "ltd", "co", "company", "corp", "corporation", "plc",
  "lp", "llp", "the", "group", "gmbh"]);

function idGuesses(name: string): string[] {
  const words = name.match(/[A-Za-z0-9]+/g) || [];
  const core = words.filter(w => !COMPANY_SUFFIXES.has(w.toLowerCase()));
  const out: string[] = [];
  for (const ws of [core.length ? core : words, words]) {
    out.push(ws.join("").toLowerCase(), ws.join("-").toLowerCase(), ws.join(""));
  }
  return [...new Set(out)].filter(g => g.length > 2);
}

export interface FoundBoard extends Board {
  board: string;
  name: string;
  openJobs: number;
  verified: boolean;
}

/** The company name a board reports, for the platforms that report one. */
async function boardName(context: sdk.ExecutionContext, board: Board): Promise<string> {
  try {
    if (board.platform === "greenhouse") {
      return (await getJson(context, `https://boards-api.greenhouse.io/v1/boards/${board.slug}`)).name || "";
    }
    if (board.platform === "workable") {
      return (await getJson(context, `https://apply.workable.com/api/v1/widget/accounts/${board.slug}`)).name || "";
    }
  } catch (e) {
    // No name to check against; the board stays unverified.
  }
  return "";
}

function firstWord(name: string): string {
  return (name.toLowerCase().match(/[a-z0-9]+/g) || []).find(w => !COMPANY_SUFFIXES.has(w)) || "";
}

/**
 * The company's board on each platform. A link is exact. A name is guessed ("Hugging Face" ->
 * huggingface, hugging-face) and only counts with at least one open job. A guess is verified when the
 * board reports a name starting with the same word, so "hdr" can't land on some other company's board.
 */
export async function findCompany(context: sdk.ExecutionContext, nameOrLink: string): Promise<FoundBoard[]> {
  const exact = parseBoard(nameOrLink);
  const candidates: Board[] = exact ? [exact]
    : idGuesses(nameOrLink).flatMap(slug => ENABLED.map(platform => ({ platform, slug })));
  if (exact) {
    requireEnabled(exact.platform);
  }
  const counts = await Promise.all(candidates.map(b => openJobs(context, b)));
  const seen = new Set<string>();
  const found: Board[] = [];
  const jobs: number[] = [];
  candidates.forEach((b, i) => {
    const key = `${b.platform}:${b.slug.toLowerCase()}`;
    if (counts[i] > 0 && !seen.has(key)) {
      seen.add(key);
      found.push(b);
      jobs.push(counts[i]);
    }
  });
  const names = await Promise.all(found.map(b => boardName(context, b)));
  const wanted = firstWord(nameOrLink);
  return found.map((b, i) => ({
    ...b,
    board: `${b.platform}:${b.slug}`,
    name: names[i] || pretty(b.slug),
    openJobs: jobs[i],
    verified: Boolean(exact) || (Boolean(names[i]) && firstWord(names[i]) === wanted),
  }));
}
