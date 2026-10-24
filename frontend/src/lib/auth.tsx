import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { onUnauthorized, tokenStore } from "@/api/client";
import { api } from "@/api/endpoints";
import type { LoginRequest, Me, RegisterRequest, Role, TokenResponse } from "@/api/types";
import { permissions, type Permission } from "./roles";

const USER_KEY = "atlas.user";

interface AuthState {
  user: Me | null;
  isAuthenticated: boolean;
  login: (body: LoginRequest) => Promise<void>;
  register: (body: RegisterRequest) => Promise<void>;
  logout: () => void;
  can: (permission: Permission) => boolean;
  role: Role | null;
}

const AuthContext = createContext<AuthState | null>(null);

function readUser(): Me | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw && tokenStore.get() ? (JSON.parse(raw) as Me) : null;
  } catch {
    return null;
  }
}

function writeUser(user: Me | null): void {
  try {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(USER_KEY);
  } catch {
    /* ignore */
  }
}

export function AuthProvider({ children, onSignedOut }: { children: ReactNode; onSignedOut?: () => void }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<Me | null>(readUser);

  const clear = useCallback(() => {
    tokenStore.set(null);
    writeUser(null);
    setUser(null);
    queryClient.clear();
  }, [queryClient]);

  // Any 401 from the API ends the session; the route guard then redirects to /login.
  useEffect(() => {
    onUnauthorized(() => {
      clear();
      onSignedOut?.();
    });
    return () => onUnauthorized(null);
  }, [clear, onSignedOut]);

  // Refresh the user's role from the server once per load (it may have changed).
  useEffect(() => {
    if (!tokenStore.get()) return;
    api.auth
      .me()
      .then((me) => {
        writeUser(me);
        setUser(me);
      })
      .catch(() => {
        /* 401 is handled globally; other errors keep the cached user */
      });
  }, []);

  const accept = useCallback(
    (token: TokenResponse) => {
      queryClient.clear();
      tokenStore.set(token.access_token);
      writeUser(token.user);
      setUser(token.user);
    },
    [queryClient],
  );

  const value = useMemo<AuthState>(
    () => ({
      user,
      role: user?.role ?? null,
      isAuthenticated: Boolean(user && tokenStore.get()),
      login: async (body) => accept(await api.auth.login(body)),
      register: async (body) => accept(await api.auth.register(body)),
      logout: clear,
      can: (permission) => permissions[permission](user?.role),
    }),
    [user, accept, clear],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
