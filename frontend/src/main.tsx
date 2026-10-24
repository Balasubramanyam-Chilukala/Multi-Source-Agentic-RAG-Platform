import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useMemo } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router/dom";
import { Toaster } from "sonner";
import { isApiError } from "@/api/client";
import { TooltipProvider } from "@/components/ui/overlays";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider, useTheme } from "@/lib/theme";
import { createAppRouter } from "./router";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      // Don't retry client errors (401/403/404/422/429); do retry transient failures once.
      retry: (count, error) => (isApiError(error) && error.status >= 400 && error.status < 500 ? false : count < 1),
    },
  },
  mutationCache: new MutationCache(),
});

function ThemedToaster() {
  const { resolved } = useTheme();
  return <Toaster theme={resolved} richColors closeButton position="bottom-right" />;
}

function App() {
  const router = useMemo(() => createAppRouter(), []);
  return (
    // On a 401 the provider clears the session; RequireAuth then redirects to /login?next=…
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <App />
          <ThemedToaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
);
