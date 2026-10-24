import { MessageSquare, MoreHorizontal, Pencil, Plus, Trash2 } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { NavLink, useNavigate } from "react-router";
import { toast } from "sonner";
import type { Conversation } from "@/api/types";
import { ConfirmDialog } from "@/components/common";
import { ErrorState, LoadingRows } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/overlays";
import { Input } from "@/components/ui/primitives";
import { useConversations, useDeleteConversation, useRenameConversation } from "@/hooks/useConversations";
import { cn, formatRelative } from "@/lib/utils";

function RenameForm({ conversation, onDone }: { conversation: Conversation; onDone: () => void }) {
  const rename = useRenameConversation();
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);
  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const title = String(new FormData(event.currentTarget).get("title")).trim();
    if (!title || title === conversation.title) return onDone();
    rename.mutate(
      { id: conversation.id, title },
      { onSuccess: onDone, onError: (e) => toast.error(e.message) },
    );
  }
  return (
    <form onSubmit={onSubmit} className="px-1 py-0.5">
      <Input
        name="title"
        defaultValue={conversation.title}
        maxLength={300}
        ref={inputRef}
        aria-label="Conversation title"
        className="h-8"
        onBlur={(e) => e.currentTarget.form?.requestSubmit()}
        onKeyDown={(e) => e.key === "Escape" && onDone()}
        disabled={rename.isPending}
      />
    </form>
  );
}

export function ConversationSidebar({ activeId, onNavigate }: { activeId?: string; onNavigate?: () => void }) {
  const { data, isPending, error, refetch } = useConversations();
  const remove = useDeleteConversation();
  const navigate = useNavigate();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Conversation | null>(null);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="p-3">
        <Button
          variant="outline"
          className="w-full justify-start"
          onClick={() => {
            navigate("/chat");
            onNavigate?.();
          }}
        >
          <Plus aria-hidden /> New conversation
        </Button>
      </div>
      <nav aria-label="Conversations" className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {isPending ? (
          <LoadingRows rows={5} className="px-1" />
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data.items.length === 0 ? (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">No conversations yet. Ask your first question.</p>
        ) : (
          <ul className="space-y-0.5">
            {data.items.map((c) => (
              <li key={c.id}>
                {renaming === c.id ? (
                  <RenameForm conversation={c} onDone={() => setRenaming(null)} />
                ) : (
                  <div className={cn("group flex items-center rounded-md", c.id === activeId ? "bg-accent text-accent-foreground" : "hover:bg-muted")}>
                    <NavLink to={`/chat/${c.id}`} onClick={onNavigate} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-sm">
                      <MessageSquare className="size-3.5 shrink-0 opacity-60" aria-hidden />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate">{c.title}</span>
                        <span className="block text-[11px] text-muted-foreground">{formatRelative(c.last_message_at ?? c.created_at)}</span>
                      </span>
                    </NavLink>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          className="mr-1 opacity-100 sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100 data-[state=open]:opacity-100"
                          aria-label={`Actions for conversation ${c.title}`}
                        >
                          <MoreHorizontal />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent>
                        <DropdownMenuItem onSelect={() => setRenaming(c.id)}>
                          <Pencil aria-hidden /> Rename
                        </DropdownMenuItem>
                        <DropdownMenuItem destructive onSelect={() => setDeleting(c)}>
                          <Trash2 aria-hidden /> Delete
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </nav>
      <ConfirmDialog
        open={Boolean(deleting)}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete this conversation?"
        description={`“${deleting?.title ?? ""}” and its messages will be permanently deleted. Run traces are kept for auditing.`}
        onConfirm={async () => {
          if (!deleting) return;
          await remove.mutateAsync(deleting.id);
          toast.success("Conversation deleted");
          if (deleting.id === activeId) navigate("/chat", { replace: true });
        }}
      />
    </div>
  );
}
