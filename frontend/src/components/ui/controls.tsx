/** Radix-based form controls: switch, checkbox, tabs. */
import { Check } from "lucide-react";
import { Checkbox as C, Switch as S, Tabs as TB } from "radix-ui";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Switch({ className, ...props }: ComponentProps<typeof S.Root>) {
  return (
    <S.Root
      className={cn(
        "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-transparent bg-input transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary",
        className,
      )}
      {...props}
    >
      <S.Thumb className="pointer-events-none block size-4 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-4" />
    </S.Root>
  );
}

export function Checkbox({ className, ...props }: ComponentProps<typeof C.Root>) {
  return (
    <C.Root
      className={cn(
        "peer flex size-4 shrink-0 items-center justify-center rounded-[4px] border border-input bg-card focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground",
        className,
      )}
      {...props}
    >
      <C.Indicator>
        <Check className="size-3" strokeWidth={3} />
      </C.Indicator>
    </C.Root>
  );
}

export const Tabs = TB.Root;

export function TabsList({ className, ...props }: ComponentProps<typeof TB.List>) {
  return (
    <TB.List
      className={cn("inline-flex h-9 items-center gap-1 rounded-md bg-muted p-1 text-muted-foreground", className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof TB.Trigger>) {
  return (
    <TB.Trigger
      className={cn(
        "inline-flex h-7 items-center gap-1.5 rounded-sm px-3 text-xs font-medium whitespace-nowrap transition-colors focus-visible:outline-2 focus-visible:outline-ring data-[state=active]:bg-card data-[state=active]:text-foreground data-[state=active]:shadow-xs [&_svg]:size-3.5",
        className,
      )}
      {...props}
    />
  );
}

export const TabsContent = TB.Content;
