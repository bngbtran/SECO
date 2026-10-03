import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { UNAUTHORIZED_EVENT, api, tokenStore } from "./api";
import type { LoginResult, Me } from "./types";

interface AuthState {
  user: Me | null;
  token: string | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Sign in with a token the API just issued (change / reset password). */
  applySession: (result: LoginResult) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState(tokenStore.get);
  const [user, setUser] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!token) {
      setUser(null);
      setReady(true);
      return;
    }
    api<Me>("/admin/auth/me")
      .then(setUser)
      .catch(() => {
        tokenStore.set(null);
        setToken(null);
      })
      .finally(() => setReady(true));
  }, [token]);

  useEffect(() => {
    const onUnauthorized = () => {
      setToken(null);
      setUser(null);
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  const applySession = useCallback((result: LoginResult) => {
    tokenStore.set(result.access_token);
    setUser(result.user);
    setToken(result.access_token);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      applySession(await api<LoginResult>("/admin/auth/login", { method: "POST", body: { email, password } }));
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    try {
      await api("/admin/auth/logout", { method: "POST" });
    } finally {
      tokenStore.set(null);
      setToken(null);
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, ready, login, logout, applySession }}>{children}</AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
