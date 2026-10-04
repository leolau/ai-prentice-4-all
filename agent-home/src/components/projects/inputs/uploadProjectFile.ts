import { friendlyError } from "@/components/projects/errors";
import { IDEMPOTENCY_HEADER } from "@/components/projects/useProjectAction";
import type { ActionResult } from "@/components/projects/useProjectAction";
import type { UploadLinkKind } from "@/components/projects/inputs/inputKinds";
import type { ProjectLink } from "@/types";

/** What `/files/upload` answers: the link it attached. */
export type UploadedLink = Pick<ProjectLink, "kind" | "profile" | "ref" | "label"> &
  Partial<ProjectLink>;

export interface UploadRequest {
  slug: string;
  file: File;
  kind: UploadLinkKind;
  /** Same key on a retry, so the route replays instead of storing twice. */
  idempotencyKey: string;
  onProgress?: (fraction: number) => void;
}

export type UploadFn = (req: UploadRequest) => Promise<ActionResult<UploadedLink>>;

function result(status: number, text: string): ActionResult<UploadedLink> {
  let data: (UploadedLink & { detail?: string }) | null = null;
  try {
    data = text ? (JSON.parse(text) as UploadedLink & { detail?: string }) : null;
  } catch {
    data = null;
  }
  if (status >= 200 && status < 300) return { ok: true, status, data, error: null };
  return {
    ok: false,
    status,
    data,
    error: friendlyError({ status, detail: data?.detail }, "Could not upload the file."),
  };
}

/**
 * POST one file to `/api/projects/{slug}/files/upload` with the link kind its
 * role maps to. Uses XHR when available so the row can show real progress.
 */
export const uploadProjectFile: UploadFn = async ({
  slug,
  file,
  kind,
  idempotencyKey,
  onProgress,
}) => {
  const path = `/api/projects/${encodeURIComponent(slug)}/files/upload`;
  const form = new FormData();
  form.append("file", file);
  form.append("kind", kind);
  form.append("label", file.name);

  if (typeof XMLHttpRequest === "undefined") {
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { [IDEMPOTENCY_HEADER]: idempotencyKey },
        body: form,
      });
      return result(res.status, await res.text().catch(() => ""));
    } catch {
      return { ok: false, status: 0, data: null, error: "Could not reach the server." };
    }
  }

  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.setRequestHeader(IDEMPOTENCY_HEADER, idempotencyKey);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => resolve(result(xhr.status, xhr.responseText));
    xhr.onerror = () =>
      resolve({ ok: false, status: 0, data: null, error: "Could not reach the server." });
    xhr.onabort = () =>
      resolve({ ok: false, status: 0, data: null, error: "The upload was cancelled." });
    xhr.send(form);
  });
};
