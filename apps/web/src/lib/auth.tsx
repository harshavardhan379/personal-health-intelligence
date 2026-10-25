"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { apiFetch, clearToken, getToken, setToken } from "@/lib/api";
import { describeFailure } from "@/lib/errors";

type User = { id: string; email: string };

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  // The saved login could not be checked because the server could not be reached (or failed).
  // The login is kept: only the server saying it is invalid (401) ends a session.
  unreachable: boolean;
  retry: () => void;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [unreachable, setUnreachable] = useState(false);

  async function loadMe() {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    try {
      const me = await apiFetch<User>("/auth/me");
      setUser(me);
      setUnreachable(false);
    } catch (err) {
      if (describeFailure(err).sessionExpired) {
        clearToken(); // the server says this login is no longer valid
        setUnreachable(false);
      } else {
        setUnreachable(true); // a network error or a server fault is not a logout
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial session check on mount
    loadMe();
  }, []);

  function retry() {
    setLoading(true);
    void loadMe();
  }

  async function login(email: string, password: string) {
    const { access_token } = await apiFetch<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setToken(access_token);
    await loadMe();
  }

  async function register(email: string, password: string) {
    const { access_token } = await apiFetch<{ access_token: string }>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setToken(access_token);
    await loadMe();
  }

  function logout() {
    clearToken();
    setUser(null);
    setUnreachable(false);
  }

  return (
    <AuthContext.Provider value={{ user, loading, unreachable, retry, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
