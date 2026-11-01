// Keyword match between a resume and a job description: the skills, tools and credentials the job names,
// and which of them the resume shows. Deterministic, so the same resume and job always score the same.

// Known skills and tools across the fields Job Alert searches. Lowercase; matched as whole terms.
const LEXICON = [
  // Software and data
  "python", "java", "javascript", "typescript", "c++", "c#", "c", "go", "golang", "rust", "ruby", "php", "swift",
  "kotlin", "scala", "r", "matlab", "sql", "nosql", "html", "css", "react", "angular", "vue", "node.js", "node",
  "django", "flask", "spring", "rails", ".net", "graphql", "rest", "api", "apis", "microservices", "linux", "unix",
  "bash", "git", "docker", "kubernetes", "terraform", "aws", "azure", "gcp", "google cloud", "ci/cd", "jenkins",
  "postgresql", "postgres", "mysql", "mongodb", "redis", "kafka", "spark", "hadoop", "airflow", "snowflake",
  "databricks", "tableau", "power bi", "excel", "pandas", "numpy", "pytorch", "tensorflow", "scikit-learn",
  "machine learning", "deep learning", "nlp", "computer vision", "llm", "llms", "data analysis", "statistics",
  "data structures", "algorithms", "distributed systems", "cloud", "security", "networking", "testing",
  "unit testing", "agile", "scrum", "jira", "figma", "ios", "android", "mobile", "frontend", "backend",
  "full stack", "full-stack", "embedded", "firmware", "rtos", "fpga", "verilog", "vhdl",
  // Electrical and hardware
  "pcb", "altium", "cadence", "spice", "ltspice", "circuit design", "analog", "digital", "power electronics",
  "signal processing", "dsp", "rf", "microcontrollers", "arduino", "raspberry pi", "oscilloscope", "soldering",
  "labview", "plc", "scada", "controls", "control systems", "simulink", "robotics", "ros", "sensors",
  // Mechanical and manufacturing
  "solidworks", "autocad", "creo", "catia", "nx", "fusion 360", "inventor", "ansys", "abaqus", "fea", "cfd",
  "gd&t", "cad", "cam", "cnc", "3d printing", "additive manufacturing", "machining", "lean", "six sigma",
  "manufacturing", "quality", "thermodynamics", "heat transfer", "fluid mechanics", "materials", "composites",
  // Civil, environmental and construction
  "civil 3d", "autocad civil 3d", "revit", "microstation", "openroads", "hydrocad", "hec-ras", "hec-hms",
  "stormwater", "drainage", "grading", "site design", "land development", "geotechnical", "structural",
  "transportation", "surveying", "gis", "arcgis", "qgis", "bim", "construction management", "estimating",
  "scheduling", "primavera", "procore", "bluebeam", "permitting", "environmental", "water resources",
  "wastewater", "hydrology", "hydraulics", "concrete", "steel", "osha", "eit", "fe", "pe", "leed",
  // Business and general
  "project management", "product management", "communication", "leadership", "teamwork", "problem solving",
  "customer service", "sales", "marketing", "finance", "accounting", "budgeting", "research", "writing",
  "presentation", "analytics", "operations", "supply chain", "crm", "salesforce", "sap", "erp",
  "spanish", "bilingual",
];

// Two-letter acronyms that are skills; any others ("SA", "HR") are ignored.
const TWO_LETTER = new Set(["ai", "ml", "ui", "ux", "qa", "rf", "pe", "fe", "it", "bi", "ee", "me", "ce"]);

// Common capitalized words and acronyms that aren't skills.
const STOP = new Set([
  "we", "you", "our", "the", "and", "for", "with", "this", "that", "your", "are", "will", "all", "who", "us",
  "usa", "u.s", "eeo", "eoe", "llc", "inc", "ltd", "ceo", "cto", "hr", "pto", "faq", "ok", "etc", "ii", "iii", "iv",
  "ada", "dei", "ein", "tbd", "n/a", "am", "pm", "est", "pst", "cst", "mst", "q1", "q2", "q3", "q4", "nyc", "sf", "la",
  "remote", "hybrid", "onsite", "gdp", "gtm", "kpi", "kpis", "okr", "okrs", "roi", "b2b", "b2c", "saas",
  "gpa", "usd", "faqs", "new", "now", "job", "role", "team", "apply", "intern", "interns", "internship", "summer", "fall", "spring", "winter",
]);

function escape(term: string): string {
  return term.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
}

function has(text: string, term: string): boolean {
  // Whole-term match; '+', '#' and '.' count as part of the term (c++, c#, node.js).
  return new RegExp(`(^|[^a-z0-9+#.])${escape(term)}($|[^a-z0-9+#])`, "i").test(text);
}

/** The skills and tools a job asks for: lexicon terms it names, plus its acronyms (PLC, HVAC, OSHA...). */
export function jobKeywords(description: string): string[] {
  const text = ` ${description.toLowerCase()} `;
  const found = new Set<string>();
  for (const term of LEXICON) {
    if (term.length > 1 && has(text, term)) {
      found.add(term);
    }
  }
  // Single letters ("C", "R") only count when written as a language: "C/C++", "in R", "R,".
  if (/\bc\/c\+\+|\bc, c\+\+/i.test(description)) {
    found.add("c");
  }
  // Acronyms: 3 to 6 capitals (PLC, HVAC, OSHA), and the two-letter ones that are real skills.
  for (const m of description.match(/\b[A-Z][A-Z0-9]{1,5}\b/g) || []) {
    const term = m.toLowerCase();
    if ((m.length >= 3 || TWO_LETTER.has(term)) && !STOP.has(term) && !/^\d/.test(term)) {
      found.add(term);
    }
  }
  // One entry per skill: "llm" and "llms", "node" and "node.js", "civil 3d" inside "autocad civil 3d".
  const terms = [...found];
  return terms.filter(t =>
    !(t.endsWith("s") && found.has(t.slice(0, -1))) &&
    !terms.some(o => o !== t && o.length > t.length && (o.startsWith(`${t}.`) || /\s/.test(o) && o.includes(t))))
    .sort();
}

export interface KeywordMatch {
  matchPercent: number;
  matched: string[];
  missing: string[];
}

export function keywordMatch(resume: string, description: string): KeywordMatch {
  const wanted = jobKeywords(description);
  const text = ` ${resume.toLowerCase()} `;
  const matched = wanted.filter(k => has(text, k));
  const missing = wanted.filter(k => !matched.includes(k));
  return {
    matchPercent: wanted.length ? Math.round((100 * matched.length) / wanted.length) : 0,
    matched,
    missing,
  };
}
