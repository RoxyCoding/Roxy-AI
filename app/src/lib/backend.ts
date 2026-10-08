// Connection to the local Python backend.
//
// In the desktop app the Tauri shell starts the backend and hands us its port
// and per-launch token (command `backend_info`). In a plain browser (for
// development) pass them in the URL: http://127.0.0.1:1420/?port=PORT&token=TOKEN

import type { ServerEvent } from "./types";

export interface BackendInfo {
  port: number;
  token: string;
}

interface ShellStatus {
  info: BackendInfo | null;
  error: string | null;
}

let info: BackendInfo | null = null;

const isTauri = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export async function discoverBackend(timeoutMs = 60000): Promise<BackendInfo> {
  const params = new URLSearchParams(window.location.search);
  const port = Number(params.get("port") || import.meta.env.VITE_ROXY_PORT || 0);
  const token = params.get("token") || import.meta.env.VITE_ROXY_TOKEN || "";
  if (port && token) {
    info = { port, token };
    return info;
  }
  if (!isTauri()) {
    throw new Error("バックエンドの接続先がありません。URL に ?port=…&token=… を指定してください（開発モード）。");
  }
  const { invoke } = await import("@tauri-apps/api/core");
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    const status = await invoke<ShellStatus>("backend_info");
    if (status.error) throw new Error(status.error);
    if (status.info) {
      info = status.info;
      return info;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error("バックエンドの起動がタイムアウトしました。ログを確認してください。");
}

export async function restartBackend(): Promise<void> {
  if (!isTauri()) return;
  const { invoke } = await import("@tauri-apps/api/core");
  info = null;
  await invoke("restart_backend");
}

function base(): string {
  if (!info) throw new Error("backend not connected");
  return `http://127.0.0.1:${info.port}`;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = unknown>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${info?.token ?? ""}` };
  let body = init.body;
  if (init.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  let res: Response;
  try {
    res = await fetch(base() + path, { ...init, body, headers: { ...headers, ...(init.headers as object) } });
  } catch {
    throw new ApiError(0, "バックエンドに接続できません");
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data?.detail) msg = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, json: unknown = {}) => api<T>(path, { method: "POST", json });
export const patch = <T>(path: string, json: unknown) => api<T>(path, { method: "PATCH", json });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });

export async function upload<T>(files: File[]): Promise<T> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  return api<T>("/api/attachments", { method: "POST", body: form });
}

export function fileUrl(id: string, thumb = false): string {
  if (!info) return "";
  return `${base()}/api/attachments/${id}/file?t=${encodeURIComponent(info.token)}${thumb ? "&thumb=1" : ""}`;
}

// WebSocket with automatic reconnect -------------------------------------------------

type Listener = (ev: ServerEvent) => void;
type ConnListener = (connected: boolean) => void;

export class EventSocket {
  private ws: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private connListeners = new Set<ConnListener>();
  private retry = 500;
  private closed = false;
  private pingTimer: number | undefined;

  connect() {
    if (!info || this.closed) return;
    const ws = new WebSocket(`ws://127.0.0.1:${info.port}/ws?t=${encodeURIComponent(info.token)}`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 500;
      this.connListeners.forEach((l) => l(true));
      this.pingTimer = window.setInterval(() => ws.readyState === 1 && ws.send('{"type":"ping"}'), 25000);
    };
    ws.onmessage = (m) => {
      try {
        const ev = JSON.parse(m.data) as ServerEvent;
        this.listeners.forEach((l) => l(ev));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = () => {
      window.clearInterval(this.pingTimer);
      this.connListeners.forEach((l) => l(false));
      if (!this.closed) {
        setTimeout(() => this.connect(), this.retry);
        this.retry = Math.min(this.retry * 2, 10000);
      }
    };
  }

  on(l: Listener) {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  }

  onConnection(l: ConnListener) {
    this.connListeners.add(l);
    return () => this.connListeners.delete(l);
  }

  close() {
    this.closed = true;
    this.ws?.close();
  }
}
