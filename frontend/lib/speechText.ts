/**
 * Speech-friendly text, the browser twin of backend/tts.py (prepare_for_speech + split_sentences).
 * The client splits replies into sentences so playback can start before the reply is finished.
 */
const ABBREVIATIONS = ["Dr", "Mr", "Mrs", "Ms", "Prof", "Sr", "Jr", "St", "vs", "etc", "e.g", "i.e", "Fig",
  "No", "approx", "Inc", "Ltd", "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct", "Nov", "Dec"];
const EMOJI = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}️‍]+/gu;

export function prepareForSpeech(text: string): string {
  let t = text || "";
  t = t.replace(/```[\s\S]*?(```|$)/g, " I've put the code block on screen. ");
  t = t.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
  t = t.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1");
  t = t.replace(/<?https?:\/\/[^\s>)\]]+>?/g, "").replace(/\bwww\.[^\s)]+/g, "");
  t = t.replace(/\s+(?:or|and|at|via|from|on|see|visit)\s*(?=[.,;:!?]|$)/gim, "");
  t = t.replace(/`([^`]+)`/g, "$1");
  t = t.replace(/^\s{0,3}#{1,6}\s*/gm, "").replace(/^\s*>\s?/gm, "");
  t = t.replace(/^\s*(?:[-*+•]|\d+[.)])\s+/gm, "").replace(/^\s*\|?\s*:?-{3,}.*$/gm, "");
  t = t.replace(/\|/g, ", ");
  t = t.replace(/(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1/g, "$2").replace(/[*_~#]+/g, " ");
  t = t.replace(EMOJI, " ");
  t = t.replace(/→/g, " to ").replace(/←/g, " from ").replace(/&/g, " and ").replace(/…/g, "...")
    .replace(/—/g, ", ").replace(/–/g, " to ");
  t = t.replace(/\s*\n\s*\n\s*/g, ". ").replace(/([^.!?:,;])\n/g, "$1. ").replace(/\n/g, " ");
  t = t.replace(/\s+([,.!?;:])/g, "$1").replace(/([,;:])(?:\s*[,;:])+/g, "$1").replace(/\.(\s*\.)+/g, ".");
  return t.replace(/\s{2,}/g, " ").trim().replace(/^[ ,;]+|[ ,;]+$/g, "");
}

export function splitSentences(text: string, maxChars = 280): string[] {
  if (!text.trim()) return [];
  let guarded = text;
  for (const a of ABBREVIATIONS) {
    guarded = guarded.replace(new RegExp(`\\b${a.replace(".", "\\.")}\\.`, "g"), `${a.replace(".", "\u0000")}\u0000`);
  }
  const parts = guarded.split(/(?<=[.!?])["”')\]]*\s+(?=["“(\[]?[A-Z0-9])/);
  const out: string[] = [];
  for (const p of parts) {
    let s = p.replace(/\u0000/g, ".").trim();
    while (s.length > maxChars) {
      let cut = Math.max(s.lastIndexOf(", ", maxChars), s.lastIndexOf("; ", maxChars));
      if (cut <= 40) cut = s.lastIndexOf(" ", maxChars);
      out.push(s.slice(0, cut + 1).trim());
      s = s.slice(cut + 1).trim();
    }
    if (s) out.push(s);
  }
  const merged: string[] = [];
  for (const s of out) {
    if (merged.length && merged[merged.length - 1].length < 24) merged[merged.length - 1] += ` ${s}`;
    else merged.push(s);
  }
  return merged;
}
