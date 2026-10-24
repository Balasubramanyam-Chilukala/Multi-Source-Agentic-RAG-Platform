/** Radix-based overlays: dialog, side sheet, dropdown menu, tooltip, popover. */
import { X } from "lucide-react";
import { Dialog as D, DropdownMenu as DM, Popover as P, Tooltip as T } from "radix-ui";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

// ---- Dialog --------------------------------------------------------------------------------

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({
  className,
  children,
  title,
  description,
  ...props
}: ComponentProps<typeof D.Content> & { title: ReactNode; description?: ReactNode }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[1px] animate-fade-in" />
      <D.Content
        className={cn(
          "fixed top-1/2 left-1/2 z-50 grid max-h-[90dvh] w-[calc(100vw-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 overflow-y-auto rounded-lg border border-border bg-card p-5 shadow-xl animate-fade-in",
          className,
        )}
        {...(description ? {} : { "aria-describedby": undefined })}
        {...props}
      >
        <div className="flex items-start justify-between gap-4">
          <div className="space-y-1">
            <D.Title className="text-base font-semibold">{title}</D.Title>
            {description && <D.Description className="text-sm text-muted-foreground">{description}</D.Description>}
          </div>
          <D.Close className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close">
            <X className="size-4" />
          </D.Close>
        </div>
        {children}
      </D.Content>
    </D.Portal>
  );
}

export function DialogFooter({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex flex-col-reverse gap-2 sm:flex-row sm:justify-end", className)} {...props} />;
}

/** Right-side drawer (used for document details and the evidence panel on small screens). */
export function SheetContent({
  className,
  children,
  title,
  description,
  bare,
  ...props
}: ComponentProps<typeof D.Content> & { title: ReactNode; description?: ReactNode; /** Content renders its own header. */ bare?: boolean }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-50 bg-black/40 animate-fade-in" />
      <D.Content
        className={cn(
          "fixed inset-y-0 right-0 z-50 flex w-full max-w-xl flex-col border-l border-border bg-card shadow-xl animate-slide-in-right",
          className,
        )}
        {...(description ? {} : { "aria-describedby": undefined })}
        {...props}
      >
        {bare ? (
          <>
            <D.Title className="sr-only">{title}</D.Title>
            <div className="min-h-0 flex-1">{children}</div>
          </>
        ) : (
          <SheetBody title={title} description={description}>
            {children}
          </SheetBody>
        )}
      </D.Content>
    </D.Portal>
  );
}

function SheetBody({ title, description, children }: { title: ReactNode; description?: ReactNode; children: ReactNode }) {
  return (
    <>
        <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
          <div className="min-w-0 space-y-1">
            <D.Title className="truncate text-base font-semibold">{title}</D.Title>
            {description && <D.Description className="text-sm text-muted-foreground">{description}</D.Description>}
          </div>
          <D.Close className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="Close">
            <X className="size-4" />
          </D.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
    </>
  );
}

// ---- Dropdown menu -------------------------------------------------------------------------

export const DropdownMenu = DM.Root;
export const DropdownMenuTrigger = DM.Trigger;

export function DropdownMenuContent({ className, ...props }: ComponentProps<typeof DM.Content>) {
  return (
    <DM.Portal>
      <DM.Content
        sideOffset={4}
        align="end"
        className={cn("z-50 min-w-40 rounded-md border border-border bg-card p-1 text-sm shadow-lg animate-fade-in", className)}
        {...props}
      />
    </DM.Portal>
  );
}

export function DropdownMenuItem({
  className,
  destructive,
  ...props
}: ComponentProps<typeof DM.Item> & { destructive?: boolean }) {
  return (
    <DM.Item
      className={cn(
        "flex cursor-default items-center gap-2 rounded-sm px-2 py-1.5 outline-none select-none data-[disabled]:opacity-50 data-[highlighted]:bg-muted [&_svg]:size-4",
        destructive && "text-danger",
        className,
      )}
      {...props}
    />
  );
}

export const DropdownMenuSeparator = (props: ComponentProps<typeof DM.Separator>) => (
  <DM.Separator className="my-1 h-px bg-border" {...props} />
);

export function DropdownMenuLabel({ className, ...props }: ComponentProps<typeof DM.Label>) {
  return <DM.Label className={cn("px-2 py-1.5 text-xs text-muted-foreground", className)} {...props} />;
}

// ---- Tooltip -------------------------------------------------------------------------------

export const TooltipProvider = T.Provider;

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <T.Root delayDuration={300}>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          sideOffset={4}
          className="z-50 max-w-xs rounded-md bg-foreground px-2 py-1 text-xs text-background shadow animate-fade-in"
        >
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}

// ---- Popover -------------------------------------------------------------------------------

export const Popover = P.Root;
export const PopoverTrigger = P.Trigger;

export function PopoverContent({ className, ...props }: ComponentProps<typeof P.Content>) {
  return (
    <P.Portal>
      <P.Content
        sideOffset={6}
        align="start"
        className={cn("z-50 w-72 rounded-md border border-border bg-card p-2 shadow-lg animate-fade-in", className)}
        {...props}
      />
    </P.Portal>
  );
}
