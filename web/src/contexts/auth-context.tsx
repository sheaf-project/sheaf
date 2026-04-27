import { createContext, useCallback, useEffect, useState } from "react";
import type { User } from "@/types/api";
import { bootstrapAuth, setAccessToken } from "@/lib/api-client";
import * as authApi from "@/lib/auth";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (
    email: string,
    password: string,
    totp_code?: string,
    captcha?: string,
    remember_device?: boolean,
  ) => Promise<void>;
  register: (
    email: string,
    password: string,
    invite_code?: string,
    newsletter_opt_in?: boolean,
    captcha?: string,
  ) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Try silent refresh on mount using HttpOnly cookie. Routes through the
  // shared single-flight in api-client so StrictMode's double-fire (and any
  // other parallel callers — e.g. queries that mount with the provider)
  // share one /v1/auth/refresh round-trip instead of racing to consume the
  // same one-shot jti.
  useEffect(() => {
    bootstrapAuth()
      .then(async (token) => {
        if (!token) {
          setAccessToken(null);
          return;
        }
        const me = await authApi.getMe();
        setUser(me);
      })
      .catch(() => {
        setAccessToken(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(
    async (
      email: string,
      password: string,
      totp_code?: string,
      captcha?: string,
      remember_device?: boolean,
    ) => {
      const tokens = await authApi.login(
        email, password, totp_code, captcha, remember_device,
      );
      setAccessToken(tokens.access_token);
      // Refresh token is set as HttpOnly cookie by the server
      const me = await authApi.getMe();
      setUser(me);
    },
    [],
  );

  const register = useCallback(
    async (
      email: string,
      password: string,
      invite_code?: string,
      newsletter_opt_in?: boolean,
      captcha?: string,
    ) => {
      const tokens = await authApi.register(
        email,
        password,
        invite_code,
        newsletter_opt_in,
        captcha,
      );
      setAccessToken(tokens.access_token);
      // Refresh token is set as HttpOnly cookie by the server
      const me = await authApi.getMe();
      setUser(me);
    },
    [],
  );

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
