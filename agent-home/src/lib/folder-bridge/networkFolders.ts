/**
 * Helpers for Folder Bridge "network folders": SMB shares mounted read-only
 * on the server through one of the user's Fortinet VPNs (Hermes
 * `/api/vpn/folders`, credential kind `smb-folder`). Shared by the BFF routes
 * and the Bridge UI, so nothing here may import server-only code.
 */
import type { NetworkFolderInput } from "@/types";

const SERVER_RE = /^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$/;
const BAD_SEGMENT_RE = /["*:<>?|]/;

function hasControlChar(text: string): boolean {
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    if (c < 0x20 || c === 0x7f) return true;
  }
  return false;
}

/**
 * Normalise what the user typed — `\\server\share\folder`,
 * `smb://server/share/folder` or `//server/share/folder` — to the
 * `//server/share[/sub...]` form Hermes stores. Returns a user-readable
 * error instead when it isn't a share path.
 */
export function normalizeSmbPath(text: string): { path: string; error: string | null } {
  const fail = (error: string) => ({ path: "", error });
  const raw = text.trim();
  if (!raw) return fail("Enter the SMB path, e.g. \\\\fileserver01\\Projects\\Tenders.");
  if (hasControlChar(raw)) return fail("The SMB path contains invalid characters.");
  let rest: string;
  if (/^smb:\/\//i.test(raw)) rest = raw.slice(6);
  else if (raw.startsWith("\\\\") || raw.startsWith("//")) rest = raw.slice(2);
  else {
    return fail(
      "The SMB path must look like \\\\server\\share\\folder or smb://server/share/folder.",
    );
  }
  const parts = rest.replace(/\\/g, "/").split("/").filter(Boolean);
  const [server, share] = parts;
  if (!server || !share) {
    return fail("Include both the server and the share name, e.g. \\\\fileserver01\\Projects.");
  }
  if (server.includes("@")) {
    return fail("Leave the login out of the path — use the share username field instead.");
  }
  if (!SERVER_RE.test(server)) {
    return fail(`"${server}" is not a valid file server name or IP address.`);
  }
  for (const seg of parts.slice(1)) {
    if (seg === "." || seg === "..") return fail("The SMB path can't contain . or .. folders.");
    if (BAD_SEGMENT_RE.test(seg)) return fail(`"${seg}" is not a valid share or folder name.`);
  }
  return { path: `//${parts.join("/")}`, error: null };
}

/** `//server/share/sub` → `\\server\share\sub`, the form Windows users know. */
export function displaySmbPath(path: string): string {
  if (!path.startsWith("//")) return path;
  return `\\\\${path.slice(2).split("/").join("\\")}`;
}

/** The server part of a share path (any accepted form), or null. */
export function smbServer(text: string): string | null {
  const { path, error } = normalizeSmbPath(text);
  return error ? null : path.slice(2).split("/")[0];
}

/**
 * Validate and whitelist a POST/PUT body for the BFF. Only the contract's
 * fields are forwarded; Hermes does the authoritative checks (path rules,
 * VPN ownership).
 */
export function parseNetworkFolderBody(
  body: unknown,
): { input: NetworkFolderInput; error: null } | { input: null; error: string } {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { input: null, error: "a JSON object body is required" };
  }
  const b = body as Record<string, unknown>;
  const input: Partial<NetworkFolderInput> = {};
  for (const key of ["name", "vpn", "share_path"] as const) {
    const v = b[key];
    if (typeof v !== "string" || !v.trim()) {
      return { input: null, error: `${key} is required` };
    }
    input[key] = v.trim();
  }
  for (const key of ["share_username", "share_domain"] as const) {
    const v = b[key];
    if (v === undefined) continue;
    if (v !== null && typeof v !== "string") {
      return { input: null, error: `${key} must be a string or null` };
    }
    input[key] = typeof v === "string" ? v.trim() || null : null;
  }
  if (b.share_password !== undefined && b.share_password !== null) {
    if (typeof b.share_password !== "string") {
      return { input: null, error: "share_password must be a string" };
    }
    if (b.share_password) input.share_password = b.share_password;
  }
  if (b.trusted_for_import !== undefined) {
    if (typeof b.trusted_for_import !== "boolean") {
      return { input: null, error: "trusted_for_import must be true or false" };
    }
    input.trusted_for_import = b.trusted_for_import;
  }
  return { input: input as NetworkFolderInput, error: null };
}
