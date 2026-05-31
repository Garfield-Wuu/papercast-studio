import { useEffect, useRef, useState } from "react";
import { subscribeWs, type StageEvent } from "@/lib/ws";
import { api } from "@/lib/api";

const MAX_BUFFER = 300;
const STORAGE_PREFIX = "papercast.evt.";
const STORAGE_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const FLUSH_INTERVAL_MS = 500;

interface CachedBuffer {
  ts: number;
  events: StageEvent[];
}

interface HistoryEnvelope {
  paper_id: string;
  events: StageEvent[];
}

function readCache(paperId: string): StageEvent[] {
  try {
    const raw = localStorage.getItem(STORAGE_PREFIX + paperId);
    if (!raw) return [];
    const data: CachedBuffer = JSON.parse(raw);
    if (Date.now() - data.ts > STORAGE_TTL_MS) {
      localStorage.removeItem(STORAGE_PREFIX + paperId);
      return [];
    }
    return Array.isArray(data.events) ? data.events : [];
  } catch {
    return [];
  }
}

function writeCache(paperId: string, events: StageEvent[]): void {
  try {
    const payload: CachedBuffer = { ts: Date.now(), events };
    localStorage.setItem(STORAGE_PREFIX + paperId, JSON.stringify(payload));
  } catch {
    // localStorage may be full; ignore — buffer is best-effort.
  }
}

function dedupeKey(ev: StageEvent): string {
  // type + stage + ts is unique enough; covers history vs WS overlap.
  return `${ev.type}|${ev.stage ?? ""}|${ev.ts ?? ""}|${ev.msg ?? ""}`;
}

function mergeEvents(...sources: StageEvent[][]): StageEvent[] {
  const seen = new Set<string>();
  const out: StageEvent[] = [];
  for (const src of sources) {
    for (const ev of src) {
      const k = dedupeKey(ev);
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(ev);
    }
  }
  // Sort by ts when available so history + cached + live appear chronologically.
  out.sort((a, b) => {
    const ta = a.ts ? Date.parse(a.ts) : 0;
    const tb = b.ts ? Date.parse(b.ts) : 0;
    return ta - tb;
  });
  return out.slice(-MAX_BUFFER);
}

/**
 * Subscribe to /ws/papers/{paperId} and keep a rolling buffer of
 * recent events. Returns the buffer + the live connection state.
 *
 * On mount we:
 *   1. seed from localStorage (events from previous tab visits)
 *   2. fetch GET /api/papers/{pid}/events for the canonical history
 *   3. open the WS for fresh events
 * Then every 500ms we flush the merged buffer back to localStorage.
 */
export function usePaperEvents(paperId: string | undefined) {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const checkConnectedRef = useRef<number | null>(null);
  const flushRef = useRef<number | null>(null);
  const eventsRef = useRef<StageEvent[]>([]);

  useEffect(() => {
    if (!paperId) return;

    const cached = readCache(paperId);
    eventsRef.current = cached;
    setEvents(cached);
    setConnected(false);

    let cancelled = false;

    // 1) Pull canonical history from the server so the timeline reflects
    //    everything that happened before this tab joined.
    api
      .get<HistoryEnvelope>(`/papers/${paperId}/events`)
      .then((resp) => {
        if (cancelled) return;
        const merged = mergeEvents(resp.events ?? [], eventsRef.current);
        eventsRef.current = merged;
        setEvents(merged);
        writeCache(paperId, merged);
      })
      .catch(() => {
        // Best-effort — log surface stays usable even if history endpoint 404s.
      });

    // 2) Open the WS for live events.
    const conn = subscribeWs(`/ws/papers/${paperId}`, (ev) => {
      if (ev.type === "ping") return;
      const merged = mergeEvents(eventsRef.current, [ev]);
      eventsRef.current = merged;
      setEvents(merged);
    });

    // 3) Throttled persistence so we don't hammer localStorage on burst events.
    flushRef.current = window.setInterval(() => {
      writeCache(paperId, eventsRef.current);
    }, FLUSH_INTERVAL_MS);

    checkConnectedRef.current = window.setInterval(() => {
      setConnected(conn.readyState() === WebSocket.OPEN);
    }, 1_000);

    return () => {
      cancelled = true;
      conn.close();
      if (flushRef.current) window.clearInterval(flushRef.current);
      if (checkConnectedRef.current) window.clearInterval(checkConnectedRef.current);
      writeCache(paperId, eventsRef.current);
    };
  }, [paperId]);

  const clear = () => {
    eventsRef.current = [];
    setEvents([]);
    if (paperId) localStorage.removeItem(STORAGE_PREFIX + paperId);
  };

  return { events, connected, clear };
}
