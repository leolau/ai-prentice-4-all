/**
 * The Folder Bridge WebSocket transport.
 *
 * Unlike `lib/app-mcp/bridge.ts` (always-on, starts the moment any
 * agent-home page mounts), this connection is opt-in: it only exists while
 * the user is on `/files/bridge` and has pressed Connect, because opening
 * it is meaningless before the user has approved at least one folder and
 * pressing Connect is itself the user's explicit "yes, let the agent reach
 * this session" gesture.
 *
 * Wire protocol is intentionally the *same* envelope app-mcp already uses
 * (`{type:"cmd",id,command}` from the server, `{type:"result",id,...}` back)
 * rather than the fuller `{id,type,method,params,result,error,ts}` shape in
 * the original spec — one working envelope per service, not two — see
 * `plans/2026-09-13-folder-bridge.md`.
 */
import {
  fileMetadata,
  listDirectoryEntries,
  readFileContent,
  searchFolder,
} from "@/lib/folder-bridge/fsOps";
import { getFolderHandle, listFolders } from "@/lib/folder-bridge/handles";
import type { ConnectionStatus, FolderCommand, SearchMatch } from "@/lib/folder-bridge/types";

interface ServiceMessage {
  type?: string;
  id?: string;
  command?: FolderCommand;
}

let ws: WebSocket | null = null;
let status: ConnectionStatus = "disconnected";
let reconnectDelay = 1000;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
// Set by the user's Disconnect action, so a stale in-flight reconnect
// doesn't resurrect a connection the user explicitly ended.
let wantConnected = false;

const listeners = new Set<() => void>();

function setStatus(next: ConnectionStatus): void {
  if (status === next) return;
  status = next;
  for (const listener of listeners) listener();
}

export function getStatus(): ConnectionStatus {
  return status;
}

export function subscribeStatus(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

async function fetchTicket(): Promise<string | null> {
  try {
    const res = await fetch("/api/app-mcp/ticket", { method: "POST" });
    if (!res.ok) return null;
    const data = (await res.json()) as { ticket?: unknown };
    return typeof data.ticket === "string" ? data.ticket : null;
  } catch {
    return null;
  }
}

async function runCommand(command: FolderCommand): Promise<Record<string, unknown>> {
  try {
    switch (command.type) {
      case "listFolders":
        return { ok: true, folders: listFolders() };
      case "listDirectory": {
        const handle = getFolderHandle(command.folderId);
        if (!handle) return { ok: false, detail: "Unknown or removed folder." };
        return { ok: true, entries: await listDirectoryEntries(handle, command.path) };
      }
      case "searchFiles": {
        const folders = listFolders().filter((f) => command.folderIds.includes(f.id));
        const matches: SearchMatch[] = [];
        for (const folder of folders) {
          const handle = getFolderHandle(folder.id);
          if (!handle) continue;
          const remaining = command.limit - matches.length;
          if (remaining <= 0) break;
          matches.push(
            ...(await searchFolder(handle, folder.id, folder.label, {
              query: command.query,
              extensions: command.extensions,
              limit: remaining,
            })),
          );
        }
        return { ok: true, matches };
      }
      case "readFile": {
        const handle = getFolderHandle(command.folderId);
        if (!handle) return { ok: false, detail: "Unknown or removed folder." };
        const result = await readFileContent(handle, command.path, {
          maxBytes: command.maxBytes,
        });
        return {
          ok: true,
          folderId: command.folderId,
          path: command.path,
          encoding: "utf-8",
          ...result,
        };
      }
      case "getFileMetadata": {
        const handle = getFolderHandle(command.folderId);
        if (!handle) return { ok: false, detail: "Unknown or removed folder." };
        const meta = await fileMetadata(handle, command.path);
        return { ok: true, folderId: command.folderId, path: command.path, ...meta };
      }
      default:
        return { ok: false, detail: `Unknown command '${(command as { type?: string }).type}'.` };
    }
  } catch (err) {
    return { ok: false, detail: err instanceof Error ? err.message : "Operation failed." };
  }
}

function scheduleReconnect(): void {
  ws = null;
  if (!wantConnected) {
    setStatus("disconnected");
    return;
  }
  setStatus("reconnecting");
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => void connect(), reconnectDelay);
  reconnectDelay = Math.min(30_000, reconnectDelay * 2);
}

async function connect(): Promise<void> {
  if (!wantConnected || ws) return;
  setStatus(status === "reconnecting" ? "reconnecting" : "connecting");
  const ticket = await fetchTicket();
  if (!ticket) {
    scheduleReconnect();
    return;
  }
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  let sock: WebSocket;
  try {
    sock = new WebSocket(
      `${proto}://${window.location.host}/app-mcp/folders/ws?ticket=${encodeURIComponent(ticket)}`,
    );
  } catch {
    scheduleReconnect();
    return;
  }
  ws = sock;
  sock.onopen = () => {
    reconnectDelay = 1000;
    setStatus("connected");
  };
  sock.onmessage = (ev) => {
    let msg: ServiceMessage;
    try {
      msg = JSON.parse(String(ev.data)) as ServiceMessage;
    } catch {
      return;
    }
    if (msg.type !== "cmd" || !msg.command) return;
    void runCommand(msg.command).then((result) => {
      try {
        sock.send(JSON.stringify({ type: "result", id: msg.id, ...result }));
      } catch {
        // Connection died mid-command; the service times out and reports it.
      }
    });
  };
  sock.onclose = () => scheduleReconnect();
  sock.onerror = () => sock.close();
}

/** User pressed Connect. */
export function connectFolderBridge(): void {
  if (wantConnected) return;
  wantConnected = true;
  reconnectDelay = 1000;
  void connect();
}

/** User pressed Disconnect. */
export function disconnectFolderBridge(): void {
  wantConnected = false;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  ws?.close();
  ws = null;
  setStatus("disconnected");
}

/** Test-only: reset module state between cases. */
export function __resetTransportForTest(): void {
  ws = null;
  status = "disconnected";
  reconnectDelay = 1000;
  wantConnected = false;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  reconnectTimer = null;
  listeners.clear();
}
