// One call that does a whole search: every title against every employer, filtered, read in full, scored
// against the resume by keywords, and returned best first. Agents get the strongest matches in seconds,
// instead of spending a minute on separate searches and lookups.

import * as sdk from "@superhuman/plugins-sdk";
import { getJob, Job, listBoard, locationMatches, requireBoard, titleMatches } from "./boards";
import { keywordMatch } from "./keywords";

const MAX_READ = 40;          // postings read in full and scored; one request each

export interface BestJob extends Omit<Job, "description"> {
  matchPercent: number;
  matched: string[];
  missing: string[];
  pay: string;
  eligibility: string;
}

/** The pay a posting states, e.g. "$30/hr" or "$86,528–$123,552/yr". Blank when it gives none. */
export function payOf(text: string): string {
  const amount = String.raw`\$\s?\d[\d,]*(?:\.\d+)?\s?[kK]?`;
  const re = new RegExp(`(${amount})(?:\\s?(?:-|–|—|to)\\s?(${amount}))?`, "g");
  const tidy = (a: string) => {
    const v = a.replace(/[\s$]/g, "");
    const n = /k$/i.test(v) ? parseFloat(v.replace(/[,k]/gi, "")) * 1000 : parseFloat(v.replace(/,/g, ""));
    return { n, text: `$${n.toLocaleString("en-US", { maximumFractionDigits: 2 })}` };
  };
  const found: Array<{ text: string; score: number }> = [];
  for (const m of text.matchAll(re)) {
    const lo = tidy(m[1]);
    const hi = m[2] ? tidy(m[2]) : undefined;
    if (!lo.n) {
      continue;
    }
    // The unit is usually written right after the amount, or the size of the number gives it away.
    const after = text.slice((m.index || 0) + m[0].length, (m.index || 0) + m[0].length + 40).toLowerCase();
    let per = /^\s*(\/|per|an|a)?\s*(hour|hr)|hourly/.test(after) ? "/hr"
      : /^\s*(\/|per|a)?\s*(week|wk)|weekly/.test(after) ? "/wk"
      : /^\s*(\/|per|a)?\s*(month|mo)\b|monthly/.test(after) ? "/mo"
      : /^\s*(\/|per|a|an)?\s*(year|yr|annum)|annual|salary|base/.test(after) ? "/yr" : "";
    if (!per) {
      per = lo.n >= 20000 ? "/yr" : lo.n < 300 ? "/hr" : "";
    }
    const range = hi && hi.n !== lo.n ? `${lo.text}–${hi.text}` : lo.text;
    found.push({ text: range + per, score: (hi ? 2 : 0) + (per ? 1 : 0) });
  }
  found.sort((a, b) => b.score - a.score);
  return found[0]?.text || "";
}

/** Who can apply, in the posting's own words: the first short sentence about degree or graduation. */
export function eligibilityOf(text: string): string {
  const sentences = text.replace(/\n+/g, ". ").split(/(?<=[.!?])\s+/);
  const about = /\b(bachelor'?s?|master'?s?|ph\.?d|degree|graduat\w*|class of|sophomores?|juniors?|freshm[ae]n|enrolled)\b/i;
  const hit = sentences.find(s => about.test(s) && s.length < 220 && !/may not be able|equal opportunity/i.test(s));
  return hit ? hit.replace(/^[-•*\s]+/, "").replace(/\.{2,}$/, ".").trim() : "";
}

// Places that clearly aren't in the US, for the country filter.
const NOT_US = /\b(canada|toronto|vancouver|montreal|uk|united kingdom|england|london|ireland|dublin|germany|berlin|munich|france|paris|spain|madrid|netherlands|amsterdam|switzerland|zurich|poland|warsaw|india|bangalore|bengaluru|hyderabad|singapore|japan|tokyo|australia|sydney|brazil|mexico|israel|tel aviv|china|korea|seoul|taiwan|emea|apac|latam)\b/i;

/** For country "US": keep US places and remote jobs that don't name another country. */
function inCountry(location: string, country: string): boolean {
  if (!country || country.toLowerCase() !== "us") {
    return true;
  }
  return !NOT_US.test(location) || /\b(us|usa|united states)\b/i.test(location);
}

export async function findBestJobs(context: sdk.ExecutionContext, opts: {
  titles: string[];
  resume: string;
  location?: string;
  companies: string[];
  exclude?: string[];
  count?: number;
  country?: string;
}): Promise<BestJob[]> {
  const queries = opts.titles.map(t => t.trim()).filter(Boolean);
  if (!queries.length) {
    throw new sdk.UserVisibleError("Give at least one job title to search for.");
  }
  const exclude = (opts.exclude || []).map(w => w.trim()).filter(Boolean);
  const lists = await Promise.allSettled(opts.companies.map(c => listBoard(context, requireBoard(c), queries)));

  // Every job whose title matches any query, in the wanted places, without excluded words; one entry per job.
  const seen = new Map<string, { job: Job; hits: number }>();
  for (const r of lists) {
    if (r.status !== "fulfilled") {
      continue;
    }
    for (const j of r.value) {
      const hits = queries.filter(q => titleMatches(j.title, q)).length;
      const key = `${j.company}|${j.title}|${j.location}`.toLowerCase();
      if (!hits || seen.has(key) || !locationMatches(j.location, j.workplace, opts.location || "") ||
        !inCountry(j.location, opts.country || "")) {
        continue;
      }
      if (exclude.some(w => titleMatches(j.title, w))) {
        continue;
      }
      seen.set(key, { job: j, hits });
    }
  }

  // Read the most promising in full: titles matching more of the queries first, then the newest.
  const shortlist = [...seen.values()]
    .sort((a, b) => b.hits - a.hits || b.job.posted.localeCompare(a.job.posted))
    .slice(0, MAX_READ)
    .map(c => c.job);
  const full = await Promise.allSettled(shortlist.map(j => (j.description ? Promise.resolve(j) : getJob(context, j.id))));

  const scored: BestJob[] = full
    .filter((r): r is PromiseFulfilledResult<Job> => r.status === "fulfilled")
    .map(({ value: j }) => {
      const { description, ...rest } = j;
      return {
        ...rest,
        ...keywordMatch(opts.resume, `${j.title}\n${description}`, j.company),
        pay: payOf(description),
        eligibility: eligibilityOf(description),
      };
    });
  return scored
    .sort((a, b) => b.matchPercent - a.matchPercent || a.missing.length - b.missing.length)
    .slice(0, opts.count || 20);
}
