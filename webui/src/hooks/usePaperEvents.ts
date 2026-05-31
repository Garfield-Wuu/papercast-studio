import { useEffect, useRef, useState } from "react";
import { subscribeWs, type StageEvent } from "@/lib/ws";

const MAX_BUFFER = 200;

/**
 * Subscribe to /ws/papers/{paperId} and keep a rolling buffer of
 * recent events. Returns the buffer + the live connection state.
 *
 * The buffer is bounded to keep memory predictable for long-running
 * tabs; older events are dropped silently (the server already
 * persists the meaningful ones in DB history).
 */
export function usePaperEvents(paperId: string | undefined) {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const checkConnectedRef = useRef<number | null>(null);

  useEffect(() => {
    if (!paperId) return;
    setEvents([]);
    setConnected(false);

    const conn = subscribeWs(`/ws/papers/${paperId}`, (ev) => {
      if (ev.type === "ping") return;
      setEvents((prev) => {
        const next = prev.length >= MAX_BUFFER
          ? [...prev.slice(prev.length - MAX_BUFFER + 1), ev]
          : [...prev, ev];
        return next;
      });
    });

    // Poll the readyState lightly so the UI can show "connecting/live".
    checkConnectedRef.current = window.setInterval(() => {
      setConnected(conn.readyState() === WebSocket.OPEN);
    }, 1_000);

    return () => {
      conn.close();
      if (checkConnectedRef.current) window.clearInterval(checkConnectedRef.current);
    };
  }, [paperId]);

  const clear = () => setEvents([]);

  return { events, connected, clear };
}
