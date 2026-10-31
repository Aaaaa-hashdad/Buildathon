// Resumes and cover letters as PDFs in the style of Jake's Resume (the popular one-page LaTeX template):
// small-caps name and section headings, a rule under each heading, bold organization with right-aligned
// dates over an italic role with right-aligned location, and compact bullets, all in a serif face.
//
// The Markdown it understands:
//   # Full Name                        name, centered, in small caps
//   555-0100 | me@x.com | linkedin...  contact line(s) right under the name, centered
//   ## Experience                      section heading with a rule
//   ### Employer | May 2024 – Aug 2024  bold left, right-aligned right (the last " | " splits them)
//   Role | City, ST                    the line right after ### : italic, with the same split
//   ### **Project** | *Python, React* | 2025   left keeps its own bold/italic
//   - Did a thing                      bullet
//   **Languages:** Python, Java        a line of its own when it starts with a bold label
//   Plain lines                        paragraphs (cover letters); one line per paragraph, blank line between

import { PDFDocument, PDFFont, PDFPage, rgb, StandardFonts } from "pdf-lib";

const PAGE_W = 612;           // US Letter, in points
const PAGE_H = 792;
const MARGIN_X = 40;
const MARGIN_Y = 38;
const BODY = 11;
const SMALL = 10;
const LEADING = 1.2;
const INK = rgb(0, 0, 0);

// The built-in PDF fonts only cover Windows-1252, so anything else is swapped for its nearest look-alike.
const REPLACEMENTS: Record<string, string> = {
  "‐": "-", "‑": "-", "‒": "-", "−": "-", " ": " ", " ": " ", "​": "",
  "→": "->", "←": "<-", "✓": "", "✔": "", "●": "•", "▪": "•",
  "‣": "•", "⁃": "-",
};

interface Run {
  text: string;
  bold?: boolean;
  italic?: boolean;
}

interface Fonts {
  regular: PDFFont;
  bold: PDFFont;
  italic: PDFFont;
  boldItalic: PDFFont;
}

/** "**Gitlytics** | *Python, Flask*" -> runs with bold and italic where marked. */
function runs(text: string, base: { bold?: boolean; italic?: boolean } = {}): Run[] {
  const out: Run[] = [];
  for (const part of text.split(/(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/)) {
    if (!part) {
      continue;
    }
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      out.push({ ...base, text: part.slice(2, -2), bold: true });
    } else if ((part.startsWith("*") && part.endsWith("*") || part.startsWith("_") && part.endsWith("_")) && part.length > 2) {
      out.push({ ...base, text: part.slice(1, -1), italic: true });
    } else {
      out.push({ ...base, text: part });
    }
  }
  return out;
}

/** Splits "left | right" on the last bar; no bar means it's all left. */
function splitRight(text: string): [string, string] {
  const i = text.lastIndexOf(" | ");
  return i < 0 ? [text, ""] : [text.slice(0, i), text.slice(i + 3)];
}

class Writer {
  page!: PDFPage;
  y = 0;

  constructor(private doc: PDFDocument, private fonts: Fonts) {
    this.newPage();
  }

  newPage() {
    this.page = this.doc.addPage([PAGE_W, PAGE_H]);
    this.y = PAGE_H - MARGIN_Y;
  }

  room(height: number) {
    if (this.y - height < MARGIN_Y) {
      this.newPage();
    }
  }

  font(run: Run): PDFFont {
    if (run.bold && run.italic) {
      return this.fonts.boldItalic;
    }
    return run.bold ? this.fonts.bold : run.italic ? this.fonts.italic : this.fonts.regular;
  }

  clean(text: string, font: PDFFont): string {
    let out = "";
    for (const ch of text) {
      const c = REPLACEMENTS[ch] ?? ch;
      try {
        font.encodeText(c);
        out += c;
      } catch (e) {
        out += "?";
      }
    }
    return out;
  }

  width(rs: Run[], size: number): number {
    return rs.reduce((sum, r) => sum + this.font(r).widthOfTextAtSize(this.clean(r.text, this.font(r)), size), 0);
  }

  drawRuns(rs: Run[], x: number, size: number) {
    for (const r of rs) {
      const font = this.font(r);
      const text = this.clean(r.text, font);
      this.page.drawText(text, { x, y: this.y, size, font, color: INK });
      x += font.widthOfTextAtSize(text, size);
    }
  }

  /** One line, with an optional right-aligned part. */
  line(left: Run[], right: Run[], size: number, opts: { center?: boolean; indent?: number } = {}) {
    const h = size * LEADING;
    this.room(h);
    this.y -= h;
    const x = opts.center ? (PAGE_W - this.width(left, size)) / 2 : MARGIN_X + (opts.indent || 0);
    this.drawRuns(left, x, size);
    if (right.length) {
      this.drawRuns(right, PAGE_W - MARGIN_X - this.width(right, size), size);
    }
  }

  /** Wrapped text, for bullets, skill lines and letter paragraphs. */
  paragraph(rs: Run[], size: number, opts: { indent?: number; bullet?: boolean } = {}) {
    const indent = opts.indent || 0;
    const maxW = PAGE_W - 2 * MARGIN_X - indent;
    const words: Run[] = [];
    for (const r of rs) {
      for (const w of r.text.split(/(\s+)/)) {
        if (w) {
          words.push({ ...r, text: w });
        }
      }
    }
    const lines: Run[][] = [[]];
    let lineW = 0;
    for (const w of words) {
      const wW = this.width([w], size);
      const isSpace = !w.text.trim();
      if (lineW + wW > maxW && !isSpace && lines[lines.length - 1].length) {
        lines.push([]);
        lineW = 0;
      }
      if (isSpace && !lines[lines.length - 1].length) {
        continue;
      }
      lines[lines.length - 1].push(w);
      lineW += wW;
    }
    lines.forEach((ln, i) => {
      const h = size * LEADING;
      this.room(h);
      this.y -= h;
      if (opts.bullet && i === 0) {
        this.page.drawCircle({ x: MARGIN_X + indent - 7, y: this.y + size * 0.3, size: 1.3, color: INK });
      }
      this.drawRuns(ln, MARGIN_X + indent, size);
    });
  }

  /** Small caps: capitals at full size, lowercase letters as smaller capitals. */
  smallCaps(text: string, size: number, opts: { center?: boolean; bold?: boolean } = {}) {
    const font = opts.bold ? this.fonts.bold : this.fonts.regular;
    const parts = [...this.clean(text, font)].map(ch =>
      /[a-z]/.test(ch) ? { ch: ch.toUpperCase(), size: size * 0.78 } : { ch, size });
    const total = parts.reduce((sum, p) => sum + font.widthOfTextAtSize(p.ch, p.size), 0);
    const h = size * 1.15;
    this.room(h);
    this.y -= h;
    let x = opts.center ? (PAGE_W - total) / 2 : MARGIN_X;
    for (const p of parts) {
      this.page.drawText(p.ch, { x, y: this.y, size: p.size, font, color: INK });
      x += font.widthOfTextAtSize(p.ch, p.size);
    }
  }

  rule() {
    this.page.drawLine({
      start: { x: MARGIN_X, y: this.y - 3 }, end: { x: PAGE_W - MARGIN_X, y: this.y - 3 },
      thickness: 0.5, color: INK,
    });
    this.y -= 5;
  }

  gap(points: number) {
    this.y -= points;
  }
}

export async function markdownToPdf(markdown: string, title: string): Promise<{ bytes: Uint8Array; pages: number }> {
  const doc = await PDFDocument.create();
  doc.setTitle(title);
  doc.setCreator("Job Alert");
  const w = new Writer(doc, {
    regular: await doc.embedFont(StandardFonts.TimesRoman),
    bold: await doc.embedFont(StandardFonts.TimesRomanBold),
    italic: await doc.embedFont(StandardFonts.TimesRomanItalic),
    boldItalic: await doc.embedFont(StandardFonts.TimesRomanBoldItalic),
  });

  let afterName = false;
  let afterEntry = false;
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length) {
      // Each line stays a line, so "Sincerely," and the name don't run together.
      paragraph.forEach(line => w.paragraph(runs(line), BODY));
      w.gap(BODY * 0.6);
      paragraph = [];
    }
  };

  for (const raw of markdown.replace(/\r/g, "").split("\n")) {
    const line = raw.trim();
    if (!line) {
      flush();
      if (afterName) {
        w.gap(4);
      }
      afterName = false;
      afterEntry = false;
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      flush();
      const level = heading[1].length;
      const text = heading[2].trim();
      if (level === 1) {
        w.smallCaps(text.replace(/\*/g, ""), 24, { center: true, bold: true });
        w.gap(2);
        afterName = true;
      } else if (level === 2) {
        w.gap(7);
        w.smallCaps(text.replace(/\*/g, ""), 13);
        w.rule();
        afterName = false;
      } else {
        const [left, right] = splitRight(text);
        w.gap(2);
        w.line(runs(left, /\*/.test(left) ? {} : { bold: true }), runs(right), BODY, { indent: 6 });
        afterName = false;
        afterEntry = true;
        continue;
      }
      afterEntry = false;
      continue;
    }
    if (afterName) {
      // The contact line(s) under the name.
      w.line(runs(line), [], SMALL, { center: true });
      continue;
    }
    const bullet = line.match(/^[-*•]\s+(.*)$/);
    if (bullet) {
      flush();
      w.paragraph(runs(bullet[1]), SMALL, { indent: 22, bullet: true });
      w.gap(0.5);
      afterEntry = false;
      continue;
    }
    if (afterEntry) {
      // The role and location under an entry heading.
      const [left, right] = splitRight(line.replace(/^[*_]|[*_]$/g, "").replace(/[*_] \| [*_]/, " | "));
      w.line(runs(left, { italic: true }), runs(right, { italic: true }), SMALL, { indent: 6 });
      afterEntry = false;
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(line)) {
      flush();
      continue;
    }
    if (/^\*\*[^*]+:\*\*/.test(line)) {
      // "**Languages:** Python, Java" lines in Skills stand alone.
      flush();
      w.paragraph(runs(line), SMALL, { indent: 6 });
      continue;
    }
    paragraph.push(line);
  }
  flush();
  return { bytes: await doc.save(), pages: doc.getPageCount() };
}
