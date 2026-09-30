import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { UNAUTHORIZED_EVENT, api, tokenStore } from "./api";
import type { Me } from "./types";

interface AuthState {
  user: Me | null;
  token: string | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
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

  const login = useCallback(async (email: string, password: string) => {
    const result = await api<{ access_token: string; user: Me }>("/admin/auth/login", {
      method: "POST",
      body: { email, password },
    });
    tokenStore.set(result.access_token);
    setUser(result.user);
    setToken(result.access_token);
  }, []);

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
    <AuthContext.Provider value={{ user, token, ready, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
