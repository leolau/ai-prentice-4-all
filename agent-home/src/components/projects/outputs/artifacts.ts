/**
 * Pure derivations for the Outputs tab and the Dashboard's Latest outputs
 * shelf: ordering, version grouping, unattached detection, file badges and
 * the fallback from `project.outputs` when the artifacts read fails.
 */
import type {
  ProjectArtifact,
  ProjectDelivery,
  ProjectOutputStatus,
  ProjectOutputWithDeliveries,
} from "@/types";

const LITERAL_ESCAPE = /\\u([0-9a-fA-F]{4})/g;

/** Literal `\uXXXX` sequences back to text (belt and braces for rows the
 * backend has not normalised yet). Lone surrogates are left as written. */
export function decodeEscapes(text: string): string;
export function decodeEscapes(text: string | null): string | null;
export function decodeEscapes(text: string | null): string | null {
  if (!text || !text.includes("\\u")) return text;
  const decoded = text.replace(LITERAL_ESCAPE, (_m, hex: string) =>
    String.fromCharCode(parseInt(hex, 16)),
  );
  return /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(decoded)
    ? text
    : decoded;
}

/** Lower-case extension of a file name or path, or null. */
export function extOf(name: string | null | undefined): string | null {
  if (!name) return null;
  const base = name.replace(/[?#].*$/, "").replace(/\/+$/, "").split("/").pop() ?? "";
  const dot = base.lastIndexOf(".");
  if (dot <= 0 || dot === base.length - 1) return null;
  const ext = base.slice(dot + 1).toLowerCase();
  return /^[a-z0-9]{1,8}$/.test(ext) ? ext : null;
}

const BADGE_ALIASES: Record<string, string> = {
  markdown: "MD",
  jpeg: "JPG",
  htm: "HTML",
  yml: "YAML",
  gdoc: "DOC",
  gsheet: "SHEET",
  gslides: "SLIDES",
};

const MIME_BADGES: Array<[RegExp, string]> = [
  [/pdf/, "PDF"],
  [/wordprocessingml|msword/, "DOCX"],
  [/spreadsheetml|ms-excel|google-apps\.spreadsheet/, "XLSX"],
  [/presentationml|ms-powerpoint|google-apps\.presentation/, "PPTX"],
  [/google-apps\.document/, "DOC"],
  [/markdown/, "MD"],
  [/^image\//, "IMG"],
  [/^text\//, "TXT"],
];

/** The short type badge on a file tile: DOCX, PDF, MD… */
export function fileBadge(
  file: Pick<ProjectArtifact, "ext" | "mime" | "title"> | { ext?: string | null; mime?: string | null; title?: string | null },
): string {
  const ext = file.ext ?? extOf(file.title ?? null);
  if (ext) return BADGE_ALIASES[ext] ?? ext.toUpperCase();
  const mime = file.mime ?? "";
  for (const [re, badge] of MIME_BADGES) if (re.test(mime)) return badge;
  return "FILE";
}

/** Newest first; ties broken by id so the order is stable. */
export function newestFirst<T extends { created_at: number; id: string }>(items: T[]): T[] {
  return [...items].sort(
    (a, b) => (b.created_at ?? 0) - (a.created_at ?? 0) || a.id.localeCompare(b.id),
  );
}

/** The shelf's tiles: the newest deliverables and drafts (notes stay on the
 * Outputs tab), `limit` of them. */
export function latestShelfItems(artifacts: ProjectArtifact[], limit = 4): ProjectArtifact[] {
  return newestFirst(artifacts.filter((a) => a.kind !== "note")).slice(0, limit);
}

/** Files no declared output owns — drafts only; working notes are expected
 * to stay unattached. */
export function unattachedFiles(artifacts: ProjectArtifact[]): ProjectArtifact[] {
  return newestFirst(artifacts.filter((a) => a.output_id === null && a.kind === "draft"));
}

export interface UnattachedGroup {
  runNo: number | null;
  files: ProjectArtifact[];
}

/** Unattached drafts grouped by the run that produced them, newest run first
 * (files outside any run last). */
export function unattachedByRun(artifacts: ProjectArtifact[]): UnattachedGroup[] {
  const groups = new Map<number | null, ProjectArtifact[]>();
  for (const file of unattachedFiles(artifacts)) {
    const list = groups.get(file.run_no) ?? [];
    list.push(file);
    groups.set(file.run_no, list);
  }
  return [...groups.entries()]
    .map(([runNo, files]) => ({ runNo, files }))
    .sort((a, b) => (b.runNo ?? -1) - (a.runNo ?? -1));
}

/** "Run 1 produced 2 files that aren't linked to any output." */
export function unattachedSentence(group: UnattachedGroup): string {
  const n = group.files.length;
  const what = n === 1 ? "1 file that isn't" : `${n} files that aren't`;
  const who = group.runNo != null ? `Run ${group.runNo}` : "A card";
  return `${who} produced ${what} linked to any output.`;
}

export interface VersionFile {
  delivery: ProjectDelivery;
  artifact: ProjectArtifact | null;
}

export interface OutputVersion {
  version: number;
  runNo: number | null;
  runId: string | null;
  deliveredAt: number;
  files: VersionFile[];
}

/**
 * An output's deliveries as versions, newest version first. Each run that
 * delivered is one version (several files in one run share it); a hand
 * delivery outside a run is a version of its own. Run numbers come from the
 * artifacts read (the project read only knows run ids).
 */
export function groupVersions(
  output: Pick<ProjectOutputWithDeliveries, "deliveries">,
  artifacts: ProjectArtifact[] = [],
): OutputVersion[] {
  const byDelivery = new Map(
    artifacts.filter((a) => a.id.startsWith("d:")).map((a) => [a.id.slice(2), a]),
  );
  const ordered = [...output.deliveries].sort(
    (a, b) => a.delivered_at - b.delivered_at || a.id.localeCompare(b.id),
  );
  const slots = new Map<string, OutputVersion>();
  for (const delivery of ordered) {
    const key = delivery.run_id ? `run:${delivery.run_id}` : `d:${delivery.id}`;
    const artifact = byDelivery.get(delivery.id) ?? null;
    let slot = slots.get(key);
    if (!slot) {
      slot = {
        version: slots.size + 1,
        runNo: artifact?.run_no ?? null,
        runId: delivery.run_id,
        deliveredAt: delivery.delivered_at,
        files: [],
      };
      slots.set(key, slot);
    }
    if (slot.runNo == null && artifact?.run_no != null) slot.runNo = artifact.run_no;
    slot.deliveredAt = Math.max(slot.deliveredAt, delivery.delivered_at);
    slot.files.push({ delivery, artifact });
  }
  return [...slots.values()].reverse();
}

/** "v2 · run 3" — the version line on a deliverable. */
export function versionLabel(v: Pick<OutputVersion, "version" | "runNo" | "runId">): string {
  if (v.runNo != null) return `v${v.version} · run ${v.runNo}`;
  return v.runId ? `v${v.version} · from a run` : `v${v.version} · added by hand`;
}

/** The state pill: "superseded" reads better than "dropped" here. */
export function outputStateLabel(status: ProjectOutputStatus): string {
  if (status === "dropped") return "superseded";
  return status.replace("_", " ");
}

/** The output the "Attach to…" shortcut suggests: the first open required
 * output with nothing delivered, else the first open one. */
export function suggestedOutput(
  outputs: ProjectOutputWithDeliveries[],
): ProjectOutputWithDeliveries | null {
  const open = [...outputs]
    .filter((o) => o.status !== "accepted" && o.status !== "dropped")
    .sort((a, b) => a.seq - b.seq);
  return (
    open.find((o) => o.required && o.deliveries.length === 0) ??
    open.find((o) => o.deliveries.length === 0) ??
    open[0] ??
    null
  );
}

/** "自學中文書寫…黃震遐_定稿v2.docx" — middle ellipsis keeps the tail that
 * tells same-prefix filenames (party names, versions, extensions) apart,
 * where plain end-truncation makes every tile read identically. */
export function middleEllipsize(title: string, max = 26): string {
  if (title.length <= max) return title;
  const tail = Math.max(8, Math.floor(max * 0.6));
  const head = max - tail - 1;
  return `${title.slice(0, head).trimEnd()}…${title.slice(-tail)}`;
}

/** The deliver-route body that attaches a produced file to an output. */
export function attachBody(file: ProjectArtifact): Record<string, string> {
  const body: Record<string, string> = { label: file.title };
  if (file.run_id) body.run_id = file.run_id;
  if (file.card_id) body.task_id = file.card_id;
  if (file.link_kind) body.link_kind = file.link_kind;
  if (file.link_ref) body.link_ref = file.link_ref;
  if (file.created_by) body.profile = file.created_by;
  return body;
}

/** Display name of a delivery's file. */
export function deliveryTitle(delivery: ProjectDelivery, fallback: string): string {
  if (delivery.label) return delivery.label;
  const ref = delivery.link_ref;
  if (!ref) return fallback;
  if (delivery.link_kind === "url") {
    try {
      const url = new URL(ref);
      return decodeURIComponent(url.pathname.split("/").filter(Boolean).pop() ?? url.hostname);
    } catch {
      return ref;
    }
  }
  const tail = ref.split("/").pop() ?? ref;
  // Storage refs are `<user>/<scope>/<uuid>-<name>`.
  return tail.replace(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-/i, "");
}

/** Fallback when the artifacts read fails: every delivery on the declared
 * outputs, as deliverable artifacts (no drafts or notes are knowable). */
export function artifactsFromOutputs(outputs: ProjectOutputWithDeliveries[]): ProjectArtifact[] {
  const out: ProjectArtifact[] = [];
  for (const output of outputs) {
    for (const v of groupVersions(output)) {
      for (const { delivery } of v.files) {
        const title = deliveryTitle(delivery, output.title);
        const isUrl = delivery.link_kind === "url" && !!delivery.link_ref;
        out.push({
          id: `d:${delivery.id}`,
          title,
          kind: "deliverable",
          ext: extOf(isUrl ? null : delivery.link_ref) ?? extOf(title),
          mime: null,
          href: isUrl ? delivery.link_ref : null,
          location: isUrl ? safeHost(delivery.link_ref) : delivery.link_kind === "file" ? "Files" : null,
          source: "delivery",
          link_kind: delivery.link_kind,
          link_ref: delivery.link_ref,
          run_id: delivery.run_id,
          run_no: null,
          card_id: delivery.task_id,
          card_title: null,
          output_id: output.id,
          output_title: output.title,
          version: v.version,
          created_at: delivery.delivered_at,
          created_by: delivery.profile,
        });
      }
    }
  }
  return newestFirst(out);
}

function safeHost(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname;
  } catch {
    return null;
  }
}

/** True when the file can be opened from the page (a link, or a storage
 * ref the shared file opener resolves). */
export function isOpenable(file: Pick<ProjectArtifact, "href" | "link_kind" | "link_ref">): boolean {
  return !!file.href || (file.link_kind === "file" && !!file.link_ref);
}

/** "run 2 · Draft MOUs" — where a file came from. */
export function provenanceLabel(file: Pick<ProjectArtifact, "run_no" | "card_title" | "output_title" | "kind">): string {
  const parts: string[] = [];
  if (file.run_no != null) parts.push(`run ${file.run_no}`);
  if (file.card_title) parts.push(file.card_title);
  if (parts.length === 0 && file.output_title) parts.push(file.output_title);
  return parts.join(" · ") || (file.kind === "deliverable" ? "added by hand" : "a card");
}
