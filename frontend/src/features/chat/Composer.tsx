import { ArrowUp, Square } from "lucide-react";
import { useState, type KeyboardEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/primitives";

export function Composer({
  onSend,
  onStop,
  streaming,
  disabled,
  toolbar,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  streaming: boolean;
  disabled?: boolean;
  toolbar?: ReactNode;
}) {
  const [text, setText] = useState("");
  const canSend = text.trim().length > 0 && !streaming && !disabled;

  function submit() {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form
      className="@container rounded-xl border border-input bg-card shadow-sm focus-within:border-primary/60 focus-within:ring-2 focus-within:ring-primary/15"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <label htmlFor="chat-input" className="sr-only">
        Ask a question about your documents
      </label>
      <Textarea
        id="chat-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask a question about your documents…"
        rows={2}
        maxLength={20000}
        className="max-h-60 min-h-14 resize-none border-0 bg-transparent px-4 pt-3 shadow-none focus-visible:outline-none"
      />
      <div className="flex items-center gap-2 px-2 pb-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">{toolbar}</div>
        <span className="hidden shrink-0 text-[11px] text-muted-foreground @xl:inline">Enter to send · Shift+Enter for a new line</span>
        {streaming ? (
          <Button type="button" size="icon" variant="secondary" onClick={onStop} aria-label="Stop generating">
            <Square className="fill-current" />
          </Button>
        ) : (
          <Button type="submit" size="icon" disabled={!canSend} aria-label="Send message">
            <ArrowUp />
          </Button>
        )}
      </div>
    </form>
  );
}
