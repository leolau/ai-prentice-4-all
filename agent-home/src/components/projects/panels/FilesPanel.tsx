"use client";

import { useRef, useState } from "react";

import { AddToProjectSheet } from "@/components/projects/AddToProjectSheet";
import { LinkRow } from "@/components/projects/panels/LinkRow";
import { BusyRegion } from "@/components/ui/BusyRegion";
import type { ProjectDetail, ProjectLink } from "@/types";

/**
 * Linked /files assets (§11.1). Card attachments join this grid once the
 * board read carries them; today the panel renders what the links store
 * knows. Empty collapses to a single "Add …" affordance rather than
 * disappearing (§13).
 *
 * Three writes live here: **link** an existing pointer (opens the shared
 * sheet), **upload** bytes straight from the browser (Storage → registry →
 * link, one round-trip), and **remove** a pointer (the authority stays in
 * the owning profile; only the link is detached).
 */
export function FilesPanel({
  project,
  archived = false,
}: {
  project: ProjectDetail;
  archived?: boolean;
}) {
  const [files, setFiles] = useState<ProjectLink[]>(project.links.file ?? []);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [busyRef, setBusyRef] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const slug = project.slug;
  const slugPath = `/api/projects/${encodeURIComponent(slug)}`;

  const remove = async (link: ProjectLink) => {
    const key = `${link.profile}:${link.ref}`;
    setBusyRef(key);
    setError(null);
    try {
      const res = await fetch(`${slugPath}/links`, {
        method: "DELETE",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          kind: link.kind,
          ref: link.ref,
          profile: link.profile,
        }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => ({}))) as { detail?: string };
        throw new Error(data.detail ?? "Could not remove the link.");
      }
      setFiles((prev) =>
        prev.filter(
          (f) => !(f.ref === link.ref && f.profile === link.profile),
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through.");
    } finally {
      setBusyRef(null);
    }
  };

  const upload = async (file: File) => {
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${slugPath}/files/upload`, {
        method: "POST",
        body: form,
      });
      const data = (await res.json().catch(() => ({}))) as {
        detail?: string;
        kind?: string;
        ref?: string;
        profile?: string;
        label?: string | null;
        added_by?: string | null;
        added_at?: number;
        project_id?: string;
        resolved?: boolean | null;
      };
      if (!res.ok) {
        throw new Error(data.detail ?? "Could not upload the file.");
      }
      const link: ProjectLink = {
        project_id: data.project_id ?? project.id,
        kind: (data.kind as "file") ?? "file",
        profile: data.profile ?? project.host_profile ?? "default",
        ref: data.ref ?? "",
        label: data.label ?? null,
        added_by: data.added_by ?? null,
        added_at: data.added_at ?? Math.floor(Date.now() / 1000),
        resolved: data.resolved ?? true,
      };
      setFiles((prev) => [...prev, link]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't go through.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return (
    <section
      id="panel-files"
      data-component="FilesPanel"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
    >
      <h2 className="text-xs uppercase tracking-wide text-[var(--color-muted)]">
        Files
      </h2>

      {error ? (
        <p className="mt-2 text-sm text-red-300" role="alert">
          {error}
        </p>
      ) : null}

      {files.length === 0 ? (
        <p className="mt-2 text-sm text-[var(--color-muted)]">
          Add a file — anything the project reads or produced belongs here.
        </p>
      ) : (
        <ul className="mt-2 flex flex-col gap-1.5">
          {files.map((link) => {
            const key = `${link.profile}:${link.ref}`;
            return (
              <li key={key} className="flex items-center gap-2">
                <div className="min-w-0 flex-1">
                  <LinkRow link={link} />
                </div>
                {archived ? null : (
                  <BusyRegion busy={busyRef === key} label="Removing…">
                    <button
                      type="button"
                      onClick={() => void remove(link)}
                      aria-label="Remove link"
                      className="shrink-0 text-xs text-[var(--color-muted)] underline disabled:opacity-40"
                    >
                      Remove
                    </button>
                  </BusyRegion>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {archived ? (
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          This project is archived — restore it (⋯) to add files.
        </p>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => setSheetOpen(true)}
            className="rounded-lg border border-[var(--color-accent)] px-3 py-1.5 text-xs font-medium text-[var(--color-accent)]"
          >
            Add link
          </button>
          <BusyRegion busy={uploading} label="Uploading…">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
              className="rounded-lg border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium disabled:opacity-40"
            >
              {uploading ? "Uploading…" : "Upload file"}
            </button>
          </BusyRegion>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void upload(f);
            }}
          />
        </div>
      )}

      {sheetOpen ? (
        <AddToProjectSheet
          onClose={() => {
            setSheetOpen(false);
            // A link added through the sheet won't be in local state; a refresh
            // re-reads from the server. Cheap, correct, and matches the rest
            // of the detail page's post-write pattern.
            window.location.reload();
          }}
          fixedSlug={slug}
          fixedName={project.name}
          prefill={{ kind: "file" }}
        />
      ) : null}
    </section>
  );
}
