// Plain text from an uploaded resume or cover letter (PDF, Word or text), so the agent can tailor it.

import * as sdk from "@superhuman/plugins-sdk";
import { strFromU8, unzipSync } from "fflate/browser";

// Files uploaded to Superhuman Docs or Go live here, and the fetcher may always read them.
const UPLOADS = /^https:\/\/([\w-]+\.)*codahosted\.io\//i;

export interface ReadDocumentResult {
  text: string;
  format: string;
}

export async function pdfText(bytes: Uint8Array): Promise<ReadDocumentResult> {
  // Loaded only when a PDF comes in, so the rest of the connector never waits on pdf.js.
  const { extractText, getDocumentProxy } = await import("unpdf");
  const pdf = await getDocumentProxy(bytes);
  const { text } = await extractText(pdf, { mergePages: true });
  return { text: tidy(text), format: "PDF" };
}

/** A .docx is a zip; the body is word/document.xml, with paragraphs as <w:p> and text runs as <w:t>. */
export function docxText(bytes: Uint8Array): ReadDocumentResult {
  const files = unzipSync(bytes, { filter: (f: { name: string }) => f.name === "word/document.xml" });
  const xml = files["word/document.xml"];
  if (!xml) {
    throw new sdk.UserVisibleError("That Word file has no document body. Try saving it again as .docx or PDF.");
  }
  const text = strFromU8(xml)
    .replace(/<w:tab\/>/g, "\t")
    .replace(/<w:br\/>|<\/w:p>/g, "\n")
    .replace(/<w:numPr>/g, "- ")          // list items become '- ' bullets
    .replace(/<[^>]+>/g, "")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
  return { text: tidy(text), format: "Word" };
}

function tidy(text: string): string {
  return text.split("\n").map(l => l.replace(/[ \t ]+/g, " ").trim()).join("\n")
    .replace(/\n{3,}/g, "\n\n").trim();
}

function formatOf(url: string, contentType: string, bytes: Uint8Array): string {
  const name = decodeURIComponent(url.split("?")[0]).toLowerCase();
  if (bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46) {     // %PDF
    return "pdf";
  }
  if (bytes[0] === 0x50 && bytes[1] === 0x4b) {     // PK: a zip, which is what .docx is
    return "docx";
  }
  if (name.endsWith(".doc") || contentType.includes("msword")) {
    return "doc";
  }
  return "text";
}

export async function readDocument(context: sdk.ExecutionContext, url: string): Promise<ReadDocumentResult> {
  if (!UPLOADS.test(url)) {
    throw new sdk.UserVisibleError("Upload the file to Superhuman (attach it in chat, or add it to the Job Alert doc) " +
      "and pass that file. Links to other sites, like Google Drive, can't be opened. You can also paste the text.");
  }
  const response = await context.fetcher.fetch({ method: "GET", url, isBinaryResponse: true });
  const bytes = new Uint8Array(response.body as Buffer);
  const contentType = String(response.headers["content-type"] || "");
  const format = formatOf(url, contentType, bytes);
  let result: ReadDocumentResult;
  if (format === "pdf") {
    result = await pdfText(bytes);
  } else if (format === "docx") {
    result = docxText(bytes);
  } else if (format === "doc") {
    throw new sdk.UserVisibleError("Old .doc files can't be read. Save it as .docx or PDF and upload it again.");
  } else {
    result = { text: tidy(strFromU8(bytes)), format: "Text" };
  }
  if (!result.text) {
    throw new sdk.UserVisibleError("That file has no readable text. It may be a scanned image: upload the original " +
      "Word file or a PDF exported from it, or paste the text.");
  }
  return result;
}
