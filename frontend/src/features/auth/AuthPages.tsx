import { BarChart3, FileSearch, ShieldCheck, Workflow } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { InlineError } from "@/components/feedback";
import { Button } from "@/components/ui/button";
import { FieldHint, Input, Label } from "@/components/ui/primitives";
import { useAuth } from "@/lib/auth";

function AuthLayout({ title, subtitle, children }: { title: string; subtitle: ReactNode; children: ReactNode }) {
  return (
    <div className="grid min-h-dvh lg:grid-cols-[1fr_1.1fr]">
      <div className="flex items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-sm space-y-6">
          <div className="flex items-center gap-2">
            <div className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <BarChart3 className="size-4" aria-hidden />
            </div>
            <span className="text-sm font-semibold">Atlas AI</span>
          </div>
          <div className="space-y-1">
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            <p className="text-sm text-muted-foreground">{subtitle}</p>
          </div>
          {children}
        </div>
      </div>
      <aside className="hidden flex-col justify-center gap-8 border-l border-border bg-card px-12 lg:flex">
        <div className="max-w-md space-y-3">
          <p className="text-xs font-semibold tracking-wider text-primary uppercase">Multi-source agentic RAG</p>
          <h2 className="text-3xl font-semibold tracking-tight">Answers grounded in your documents — with the receipts.</h2>
          <p className="text-sm text-muted-foreground">
            Every answer links to the passages it came from. When the evidence isn't there, Atlas says so instead of guessing.
          </p>
        </div>
        <ul className="max-w-md space-y-4 text-sm">
          {[
            { icon: FileSearch, title: "Hybrid retrieval", text: "Dense + lexical search with optional reranking across PDFs, docs, pages and data files." },
            { icon: Workflow, title: "Inspectable agent", text: "See each step: routing, retrieval attempts, scores, citation checks and token usage." },
            { icon: ShieldCheck, title: "Measured quality", text: "Reference-based evaluation of recall, MRR, nDCG and answer faithfulness." },
          ].map(({ icon: Icon, title: t, text }) => (
            <li key={t} className="flex gap-3">
              <div className="mt-0.5 rounded-md bg-accent p-2 text-accent-foreground">
                <Icon className="size-4" aria-hidden />
              </div>
              <div>
                <p className="font-medium">{t}</p>
                <p className="text-muted-foreground">{text}</p>
              </div>
            </li>
          ))}
        </ul>
      </aside>
    </div>
  );
}

export function LoginPage() {
  const { login } = useAuth();
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      // On success the guest-only route guard redirects to ?next (or the dashboard).
      await login({ email: String(form.get("email")), password: String(form.get("password")) });
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthLayout title="Sign in" subtitle="Welcome back. Sign in to your workspace.">
      <form className="space-y-4" onSubmit={onSubmit} noValidate={false}>
        <div className="space-y-1.5">
          <Label htmlFor="email">Email</Label>
          <Input id="email" name="email" type="email" autoComplete="email" required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input id="password" name="password" type="password" autoComplete="current-password" required />
        </div>
        <InlineError error={error} />
        <Button type="submit" className="w-full" loading={pending}>
          Sign in
        </Button>
      </form>
      <p className="text-sm text-muted-foreground">
        New to Atlas?{" "}
        <Link to="/register" className="font-medium text-primary hover:underline">
          Create a workspace
        </Link>
      </p>
    </AuthLayout>
  );
}

export function RegisterPage() {
  const { register } = useAuth();
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password"));
    if (password.length < 10) {
      setPasswordError("Use at least 10 characters.");
      return;
    }
    setPasswordError(null);
    setPending(true);
    setError(null);
    try {
      await register({
        email: String(form.get("email")),
        password,
        display_name: String(form.get("display_name")),
        workspace_name: String(form.get("workspace_name")),
      });
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthLayout title="Create your workspace" subtitle="You'll be the workspace owner. You can invite teammates later.">
      <form className="space-y-4" onSubmit={onSubmit}>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="display_name">Your name</Label>
            <Input id="display_name" name="display_name" autoComplete="name" required maxLength={200} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="workspace_name">Workspace</Label>
            <Input id="workspace_name" name="workspace_name" autoComplete="organization" required maxLength={200} />
          </div>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="email">Work email</Label>
          <Input id="email" name="email" type="email" autoComplete="email" required />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="new-password"
            required
            minLength={10}
            maxLength={128}
            aria-invalid={passwordError ? true : undefined}
            aria-describedby="password-hint"
          />
          <FieldHint id="password-hint" className={passwordError ? "text-danger" : undefined}>
            {passwordError ?? "At least 10 characters."}
          </FieldHint>
        </div>
        <InlineError error={error} />
        <Button type="submit" className="w-full" loading={pending}>
          Create workspace
        </Button>
      </form>
      <p className="text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link to="/login" className="font-medium text-primary hover:underline">
          Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
