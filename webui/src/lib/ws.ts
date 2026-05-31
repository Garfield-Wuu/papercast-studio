/**
 * Auto-reconnecting WebSocket subscription.
 *
 * StageEvent type is hand-written here — FastAPI doesn't export
 * WebSocket schemas via OpenAPI, so the codegen in api.gen.ts misses
 * it. The shape mirrors `papercast.server.schemas.StageEvent`; if
 * fields change there, update them here too.
 */

import type { components } from "./api.gen";

export type Stage = components["schemas"]["Stage"];

export type StageEventType =
  | "stage_started"
  | "stage_advanced"
  | "log"
  | "progress"
  | "needs_review"
  | "approved"
  | "failed"
  | "paper_registered"
  | "paper_deleted"
  | "config_changed"
  | "ping";

export interface StageEvent {
  type: StageEventType;
  paper_id?: string | null;
  stage?: Stage | null;
  msg?: string | null;
  level?: "info" | "warn" | "error" | null;
  progress?: [number, number] | null;
  error?: string | null;
  ts?: string;
}

type Listener = (ev: StageEvent) => void;

export interface WsConnection {
  readonly path: string;
  close(): void;
  readyState(): number;
}

const RECONNECT_BASE_MS = 800;
const RECONNECT_MAX_MS = 30_000;

export function subscribeWs(path: string, onEvent: Listener): WsConnection {
  let stopped = false;
  let attempt = 0;
  let socket: WebSocket | null = null;

  const open = () => {
    if (stopped) return;
    const url = buildWsUrl(path);
    socket = new WebSocket(url);

    socket.onopen = () => {
      attempt = 0;
    };

    socket.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data) as StageEvent;
        onEvent(data);
      } catch (e) {
        console.warn("[ws] bad payload:", msg.data, e);
      }
    };

    socket.onerror = () => {
      // The browser fires onclose right after; let the reconnect path handle it.
    };

    socket.onclose = () => {
      socket = null;
      if (stopped) return;
      attempt += 1;
      const delay = Math.min(
        RECONNECT_MAX_MS,
        RECONNECT_BASE_MS * 2 ** Math.min(attempt, 6),
      );
      setTimeout(open, delay);
    };
  };

  open();

  return {
    path,
    close() {
      stopped = true;
      socket?.close();
    },
    readyState() {
      return socket?.readyState ?? WebSocket.CLOSED;
    },
  };
}

function buildWsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}${path}`;
}
