/**
 * remark plugin that turns citation markers like "[S1]" in answer text into `<cite>` elements
 * (rendered as clickable chips), and optionally wraps unsupported sentences in `<mark>`.
 *
 * It only touches mdast `text` nodes, so markers inside code spans/blocks are left alone, and it
 * never produces raw HTML: the output is ordinary hast elements that react-markdown maps to
 * React components.
 */

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, unknown> };
}

export const MARKER_PATTERN = /\[(S\d+)\]/g;

export interface RemarkCitationsOptions {
  /** Sentences (claim texts) to highlight as "general context (not from sources)". */
  unsupported?: string[];
}

function citationNode(marker: string): MdNode {
  return {
    type: "citation",
    data: { hName: "cite", hProperties: { dataMarker: marker } },
    children: [{ type: "text", value: marker }],
  };
}

/** Split text into plain text and citation nodes. */
export function splitMarkers(value: string): MdNode[] {
  const out: MdNode[] = [];
  let last = 0;
  for (const match of value.matchAll(MARKER_PATTERN)) {
    const index = match.index ?? 0;
    if (index > last) out.push({ type: "text", value: value.slice(last, index) });
    out.push(citationNode(match[1] as string));
    last = index + match[0].length;
  }
  if (last < value.length) out.push({ type: "text", value: value.slice(last) });
  return out;
}

/** Normalize a claim for matching against rendered text (markdown emphasis characters removed). */
function normalizeClaim(text: string): string {
  return text.replace(/[*_`]+/g, "").replace(/\s+/g, " ").trim();
}

function findRanges(value: string, claims: string[]): Array<[number, number]> {
  const ranges: Array<[number, number]> = [];
  for (const claim of claims) {
    if (claim.length < 3) continue;
    let from = 0;
    for (;;) {
      const at = value.indexOf(claim, from);
      if (at === -1) break;
      ranges.push([at, at + claim.length]);
      from = at + claim.length;
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const r of ranges) {
    const prev = merged[merged.length - 1];
    if (prev && r[0] <= prev[1]) prev[1] = Math.max(prev[1], r[1]);
    else merged.push([r[0], r[1]]);
  }
  return merged;
}

export function transformText(value: string, claims: string[]): MdNode[] {
  const ranges = claims.length ? findRanges(value, claims) : [];
  if (ranges.length === 0) return splitMarkers(value);
  const out: MdNode[] = [];
  let last = 0;
  for (const [start, end] of ranges) {
    if (start > last) out.push(...splitMarkers(value.slice(last, start)));
    out.push({
      type: "unsupportedClaim",
      data: { hName: "mark", hProperties: { className: ["unsupported"], title: "General context — not from your sources" } },
      children: splitMarkers(value.slice(start, end)),
    });
    last = end;
  }
  if (last < value.length) out.push(...splitMarkers(value.slice(last)));
  return out;
}

function walk(node: MdNode, claims: string[]): void {
  if (!node.children) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && typeof child.value === "string") {
      next.push(...transformText(child.value, claims));
    } else {
      if (child.type !== "link" && child.type !== "linkReference") walk(child, claims);
      next.push(child);
    }
  }
  node.children = next;
}

export function remarkCitations(options: RemarkCitationsOptions = {}) {
  const claims = (options.unsupported ?? []).map(normalizeClaim).filter(Boolean);
  return (tree: unknown) => {
    walk(tree as MdNode, claims);
  };
}

/** All distinct markers referenced in an answer, in order of first appearance. */
export function extractMarkers(content: string): string[] {
  const seen = new Set<string>();
  for (const match of content.matchAll(MARKER_PATTERN)) seen.add(match[1] as string);
  return [...seen];
}
