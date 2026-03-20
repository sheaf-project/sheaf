import { createContext, useCallback, useEffect, useState } from "react";
import type { User } from "@/types/api";
import { setAccessToken } from "@/lib/api-client";
import * as authApi from "@/lib/auth";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string, totp_code?: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Try silent refresh on mount using HttpOnly cookie
  useEffect(() => {
    fetch("/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
      credentials: "same-origin",
    })
      .then(async (resp) => {
        if (!resp.ok) throw new Error("refresh failed");
        const data = await resp.json();
        setAccessToken(data.access_token);
        const me = await authApi.getMe();
        setUser(me);
      })
      .catch(() => {
        setAccessToken(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email: string, password: string, totp_code?: string) => {
    const tokens = await authApi.login(email, password, totp_code);
    setAccessToken(tokens.access_token);
    // Refresh token is set as HttpOnly cookie by the server
    const me = await authApi.getMe();
    setUser(me);
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    const tokens = await authApi.register(email, password);
    setAccessToken(tokens.access_token);
    // Refresh token is set as HttpOnly cookie by the server
    const me = await authApi.getMe();
    setUser(me);
  }, []);

  const refreshUser = useCallback(async () => {
    const me = await authApi.getMe();
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setAccessToken(null);
      setUser(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}
