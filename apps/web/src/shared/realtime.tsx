import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { RealtimeEvent } from "./types";

export interface LoggedEvent extends RealtimeEvent {
  receivedAt: number;
}

type Listener = (event: RealtimeEvent) => void;

interface RealtimeState {
  connected: boolean;
  /** Most recent events first (capped). */
  events: LoggedEvent[];
  /** Total power (kW) sampled every few seconds from session telemetry. */
  powerHistory: number[];
  subscribe: (listener: Listener) => () => void;
}

const RealtimeContext = createContext<RealtimeState | null>(null);

const MAX_EVENTS = 200;
const POWER_SAMPLE_MS = 5000;
const POWER_POINTS = 60;
// A charging station whose last telemetry is older than this no longer counts.
const POWER_STALE_MS = 45_000;

export function RealtimeProvider({ token, children }: { token: string; children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const [events, setEvents] = useState<LoggedEvent[]>([]);
  const [powerHistory, setPowerHistory] = useState<number[]>([]);
  const listeners = useRef(new Set<Listener>());
  const power = useRef(new Map<string, { kw: number; at: number }>());

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: number | undefined;
    let closed = false;

    const connect = () => {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${window.location.host}/ws?token=${encodeURIComponent(token)}`);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, 3000);
      };
      socket.onmessage = (message) => {
        let event: RealtimeEvent;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }

        if (event.station_id && typeof event.power_kw === "number") {
          const charging = event.status === "CHARGING" || event.status === "ACTIVE";
          power.current.set(event.station_id, { kw: charging ? event.power_kw : 0, at: Date.now() });
        }
        if (event.station_id && (event.event === "STOPPED" || event.event === "WAITING_PAYMENT")) {
          power.current.delete(event.station_id);
        }

        setEvents((prev) => [{ ...event, receivedAt: Date.now() }, ...prev].slice(0, MAX_EVENTS));
        listeners.current.forEach((listener) => listener(event));
      };
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      socket?.close();
    };
  }, [token]);

  useEffect(() => {
    const id = window.setInterval(() => {
      const now = Date.now();
      let total = 0;
      power.current.forEach(({ kw, at }) => {
        if (now - at < POWER_STALE_MS) total += kw;
      });
      setPowerHistory((prev) => [...prev, total].slice(-POWER_POINTS));
    }, POWER_SAMPLE_MS);
    return () => window.clearInterval(id);
  }, []);

  // Stable identity: consumers subscribe once instead of on every event.
  const subscribe = useCallback((listener: Listener) => {
    listeners.current.add(listener);
    return () => {
      listeners.current.delete(listener);
    };
  }, []);

  return (
    <RealtimeContext.Provider value={{ connected, events, powerHistory, subscribe }}>
      {children}
    </RealtimeContext.Provider>
  );
}

export function useRealtime() {
  const ctx = useContext(RealtimeContext);
  if (!ctx) throw new Error("useRealtime outside RealtimeProvider");
  return ctx;
}

/**
 * Run `onEvent` for realtime events matching `filter` (debounced), typically a
 * silent reload of the page data.
 */
export function useRealtimeRefresh(
  onEvent: () => void,
  filter: (event: RealtimeEvent) => boolean = () => true,
  debounceMs = 600,
) {
  const { subscribe } = useRealtime();
  const saved = useRef({ onEvent, filter });
  saved.current = { onEvent, filter };

  useEffect(() => {
    let timer: number | undefined;
    const unsubscribe = subscribe((event) => {
      if (!saved.current.filter(event)) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => saved.current.onEvent(), debounceMs);
    });
    return () => {
      window.clearTimeout(timer);
      unsubscribe();
    };
  }, [subscribe, debounceMs]);
}
