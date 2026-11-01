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

// Places that clearly aren't in the US, for the country filter: countries, regions, and cities often given alone.
const NOT_US = new RegExp("\\b(" + [
  "canada", "mexico", "brazil", "argentina", "chile", "colombia", "peru", "uruguay", "costa rica",
  "uk", "united kingdom", "england", "scotland", "wales", "ireland", "france", "germany", "spain", "portugal",
  "italy", "netherlands", "belgium", "luxembourg", "switzerland", "austria", "denmark", "sweden", "norway",
  "finland", "iceland", "poland", "czech", "czechia", "slovakia", "hungary", "romania", "bulgaria", "serbia",
  "croatia", "slovenia", "greece", "turkey", "ukraine", "estonia", "latvia", "lithuania", "israel", "uae",
  "united arab emirates", "saudi arabia", "qatar", "egypt", "nigeria", "kenya", "south africa", "india",
  "pakistan", "bangladesh", "sri lanka", "singapore", "malaysia", "indonesia", "philippines", "vietnam",
  "thailand", "japan", "korea", "china", "hong kong", "taiwan", "australia", "new zealand",
  "toronto", "vancouver", "montreal", "london", "dublin", "paris", "berlin", "munich", "amsterdam", "zurich",
  "madrid", "barcelona", "lisbon", "warsaw", "krakow", "prague", "aarhus", "copenhagen", "stockholm", "oslo",
  "helsinki", "belgrade", "bucharest", "tel aviv", "bangalore", "bengaluru", "hyderabad", "pune", "tokyo",
  "seoul", "sydney", "melbourne", "sao paulo", "emea", "apac", "latam",
].join("|") + ")\\b", "i");

const US_STATES: Record<string, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado",
  CT: "Connecticut", DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii",
  ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana",
  ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi",
  MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey",
  NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma",
  OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota",
  TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington",
  WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming",
};

/** "Houston, TX, College Station" -> ["TX", "Texas"]: each state named or abbreviated, both ways. */
function statesIn(location: string): string[] {
  const out = new Set<string>();
  for (const [code, name] of Object.entries(US_STATES)) {
    if (new RegExp(`\\b${code}\\b`).test(location) || new RegExp(`\\b${name}\\b`, "i").test(location)) {
      out.add(code);
      out.add(name);
    }
  }
  return [...out];
}

/**
 * Keep jobs in the country: for "US" (the default), US places and remote jobs that don't name another
 * country. "any" turns the filter off.
 */
function inCountry(location: string, country: string): boolean {
  if ((country || "US").toLowerCase() !== "us") {
    return true;
  }
  return !NOT_US.test(location) || /\b(us|usa|united states)\b/i.test(location);
}

/** For a bachelor's student: drop high-school roles and ones only open to master's or PhD students ("BS/MS/PhD" stays). */
function fitsDegree(j: BestJob, degree: string): boolean {
  if (degree.toLowerCase() !== "bachelors") {
    return true;
  }
  if (/\bhigh school\b/i.test(j.title)) {
    return false;
  }
  const undergrad = /\b(bs|ba|b\.s|bachelor'?s?|undergrad\w*)\b/i;
  const graduate = /\b(ms|m\.s|master'?s?|ph\.?d|graduate degree|doctoral)\b/i;
  if (graduate.test(j.title) && !undergrad.test(j.title)) {
    return false;
  }
  return !(graduate.test(j.eligibility) && !undergrad.test(j.eligibility));
}

export async function findBestJobs(context: sdk.ExecutionContext, opts: {
  titles: string[];
  resume: string;
  location?: string;
  companies: string[];
  exclude?: string[];
  count?: number;
  country?: string;
  degree?: string;
  widened?: "state" | "country" | "titles";
}): Promise<BestJob[]> {
  const queries = opts.titles.map(t => t.trim()).filter(Boolean);
  if (!queries.length) {
    throw new sdk.UserVisibleError("Give at least one job title to search for.");
  }
  const exclude = (opts.exclude || []).map(w => w.trim()).filter(Boolean);
  if (exclude.some(w => /^senior$/i.test(w))) {
    exclude.push("sr");           // "Sr. Software Engineer"
  }
  const lists = await Promise.allSettled(opts.companies.map(c => listBoard(context, requireBoard(c), queries)));

  // Every job whose title matches any query, in the wanted places, without excluded words; one entry per job.
  const seen = new Map<string, { job: Job; hits: number }>();
  for (const r of lists) {
    if (r.status !== "fulfilled") {
      continue;
    }
    for (const j of r.value) {
      const hits = queries.filter(q => titleMatches(j.title, q)).length;
      // One entry per job title at a company: the same opening in three cities is one job with three places.
      const key = `${j.company}|${j.title}`.toLowerCase();
      if (!hits || !locationMatches(j.location, j.workplace, opts.location || "") ||
        !inCountry(j.location, opts.country || "")) {
        continue;
      }
      if (exclude.some(w => titleMatches(j.title, w))) {
        continue;
      }
      const same = seen.get(key);
      if (same) {
        if (!same.job.location.includes(j.location)) {
          same.job = { ...same.job, location: `${same.job.location}; ${j.location}` };
        }
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
    .map((r, i) => (r.status === "fulfilled" ? { ...r.value, location: shortlist[i].location } : undefined))
    .filter((j): j is Job => Boolean(j))
    .map(j => {
      const { description, ...rest } = j;
      return {
        ...rest,
        ...keywordMatch(opts.resume, `${j.title}\n${description}`, j.company),
        pay: payOf(description),
        eligibility: eligibilityOf(description),
      };
    });
  const count = opts.count || 20;
  const kept = scored
    .filter(j => fitsDegree(j, opts.degree || ""))
    .sort((a, b) => b.matchPercent - a.matchPercent || a.missing.length - b.missing.length);
  if (kept.length >= count || opts.widened === "titles") {
    return kept.slice(0, count);
  }
  // Too few: widen one step and top up. The country filter always stays.
  //   1. the places given -> their states, plus remote ("Houston, TX" -> "TX, Texas, Remote")
  //   2. -> anywhere in the country
  //   3. -> each title broadened to its last word ("software engineer intern" -> "intern")
  const states = statesIn(opts.location || "");
  const wider = opts.location && !opts.widened && states.length
    ? { ...opts, location: [...states, "Remote"].join(", "), widened: "state" as const }
    : opts.location
      ? { ...opts, location: "", widened: "country" as const }
      : { ...opts, titles: [...new Set(queries.map(q => q.split(/\s+/).pop() || q))], widened: "titles" as const };
  const more = await findBestJobs(context, wider);
  const ids = new Set(kept.map(j => j.id));
  return [...kept, ...more.filter(j => !ids.has(j.id))].slice(0, count);
}
