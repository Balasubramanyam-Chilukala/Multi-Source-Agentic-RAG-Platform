import { memo, useMemo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation } from "@/api/types";
import { remarkCitations } from "@/lib/remarkCitations";
import { cn, safeExternalUrl } from "@/lib/utils";

export interface AnswerMarkdownProps {
  content: string;
  citations: Citation[];
  /** Called when a citation chip is clicked. */
  onCitationClick?: (marker: string) => void;
  activeMarker?: string | null;
  /** Claim texts to highlight as general context (only when the toggle is on). */
  highlightUnsupported?: string[];
}

export function CitationChip({
  marker,
  citation,
  active,
  onClick,
}: {
  marker: string;
  citation: Citation | undefined;
  active?: boolean;
  onClick?: (marker: string) => void;
}) {
  if (!citation) {
    // A marker with no matching citation (should not happen after server validation): plain text.
    return <span className="text-muted-foreground">[{marker}]</span>;
  }
  const label = `Source ${marker}: ${citation.document_title}${citation.source_deleted ? " (source deleted)" : ""}`;
  return (
    <button
      type="button"
      onClick={() => onClick?.(marker)}
      aria-label={label}
      title={label}
      aria-pressed={active}
      className={cn(
        "mx-0.5 inline-flex h-[1.15rem] -translate-y-px items-center rounded px-1 align-middle font-mono text-[10px] leading-none font-semibold transition-colors",
        citation.source_deleted
          ? "bg-muted text-muted-foreground line-through"
          : active
            ? "bg-primary text-primary-foreground"
            : "bg-accent text-accent-foreground hover:bg-primary hover:text-primary-foreground",
      )}
    >
      {marker}
    </button>
  );
}

function AnswerMarkdownImpl({ content, citations, onCitationClick, activeMarker, highlightUnsupported }: AnswerMarkdownProps) {
  const byMarker = useMemo(() => new Map(citations.map((c) => [c.marker, c])), [citations]);
  const plugins = useMemo(
    () => [remarkGfm, [remarkCitations, { unsupported: highlightUnsupported ?? [] }] as const],
    [highlightUnsupported],
  );

  const components = useMemo<Components>(
    () => ({
      cite: ({ node }) => {
        const marker = String(node?.properties?.dataMarker ?? "");
        return <CitationChip marker={marker} citation={byMarker.get(marker)} active={activeMarker === marker} onClick={onCitationClick} />;
      },
      a: ({ href, children }) => {
        const safe = safeExternalUrl(href);
        return safe ? (
          <a href={safe} target="_blank" rel="noopener noreferrer nofollow">
            {children}
          </a>
        ) : (
          <span>{children}</span>
        );
      },
      // Never load remote images from model output (tracking pixels); show them as links instead.
      img: ({ src, alt }) => {
        const safe = typeof src === "string" ? safeExternalUrl(src) : null;
        return safe ? (
          <a href={safe} target="_blank" rel="noopener noreferrer nofollow">
            [image: {alt || safe}]
          </a>
        ) : (
          <span>[image{alt ? `: ${alt}` : ""}]</span>
        );
      },
    }),
    [byMarker, activeMarker, onCitationClick],
  );

  return (
    <div className="prose-atlas text-sm">
      {/* skipHtml: raw HTML in model output is dropped, never rendered */}
      <Markdown remarkPlugins={plugins as never} components={components} skipHtml>
        {content}
      </Markdown>
    </div>
  );
}

export const AnswerMarkdown = memo(AnswerMarkdownImpl);
