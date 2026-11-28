import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import type { Health, Status } from "../types";

interface SystemState {
  status: Status | null;
  health: Health | null;
  reachable: boolean | null; // null while the first check runs
  refresh: () => void;
}

const SystemContext = createContext<SystemState>({ status: null, health: null, reachable: null, refresh: () => {} });

export function SystemProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);

  const refresh = useCallback(() => {
    Promise.all([api.status(), api.health()])
      .then(([s, h]) => {
        setStatus(s);
        setHealth(h);
        setReachable(true);
      })
      .catch(() => setReachable(false));
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return <SystemContext.Provider value={{ status, health, reachable, refresh }}>{children}</SystemContext.Provider>;
}

export const useSystem = () => useContext(SystemContext);
