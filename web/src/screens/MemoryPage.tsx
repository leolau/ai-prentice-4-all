import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { AlertTriangle, Brain, Map as MapIcon, RefreshCw, Search } from "lucide-react";
import * as Plot from "@observablehq/plot";
import { fetchJSON } from "@/lib/api";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useI18n } from "@/i18n";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Stats } from "@nous-research/ui/ui/components/stats";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface MemorySpace {
  column_dim: number | null;
  rows_by_model: Record<string, number>;
  configured_model: string;
  healthy: boolean;
}

interface MemorySummary {
  space: MemorySpace;
  totals: { memories: number; documents: number; chunks: number };
  by_owner: Record<string, number>;
  by_topic: Record<string, number>;
  by_kind: Record<string, number>;
  growth: { day: string; count: number }[];
  recall_use: {
    never_used: number;
    used_7d: number;
    top: { id: string; text: string; truncated: boolean; uses: number; last_used: string | null }[];
  };
}

interface MemoryRow {
  id: string;
  owner_user_id: string;
  visibility: string;
  kind: string;
  topic: string | null;
  text: string;
  truncated: boolean;
  created_at: string | null;
  uses: number;
  last_used: string | null;
  elevated: boolean;
  provenance: string;
  score: number | null;
}

interface RowsResponse {
  rows: MemoryRow[];
  total: number;
  limit: number;
  offset: number;
}

interface ProjectionPoint {
  id: string;
  x: number;
  y: number;
  owner_user_id: string;
  topic: string | null;
  kind: string;
  elevated: boolean;
  provenance: string;
  label: string;
}

interface ProjectionData {
  algorithm: string | null;
  computed_at: string | null;
  stale: boolean;
  unprojected_count: number;
  points: ProjectionPoint[];
}

interface DocumentItem {
  id: string;
  owner_user_id: string;
  visibility: string;
  source_kind: string;
  source_ref: string;
  title: string;
  chunk_count: number;
  ingested_at: string | null;
}

interface DocumentsResponse {
  documents: DocumentItem[];
  total: number;
}

const PAGE_SIZE = 50;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function MemoryPage() {
  const [summary, setSummary] = useState<MemorySummary | null>(null);
  const [rowsData, setRowsData] = useState<RowsResponse | null>(null);
  const [projection, setProjection] = useState<ProjectionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQ, setSearchQ] = useState("");
  const [activeQ, setActiveQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedPoint, setSelectedPoint] = useState<ProjectionPoint | null>(null);
  const [queryText, setQueryText] = useState("");
  const [queryResult, setQueryResult] = useState<{
    x: number | null;
    y: number | null;
    nearest: { id: string; score: number }[];
    degraded?: boolean;
  } | null>(null);
  const [recallFloor, setRecallFloor] = useState(0.0);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [colorMode, setColorMode] = useState<"topic" | "document">("topic");
  const [filterDocId, setFilterDocId] = useState<string | null>(null);
  const { t } = useI18n();
  const { setAfterTitle, setEnd } = usePageHeader();
  const plotRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    const rowsUrl =
      `/api/memory/explorer/rows?limit=${PAGE_SIZE}&offset=${offset}` +
      (activeQ ? `&q=${encodeURIComponent(activeQ)}` : "");
    Promise.all([
      fetchJSON<MemorySummary>("/api/memory/explorer/summary"),
      fetchJSON<RowsResponse>(rowsUrl),
      fetchJSON<ProjectionData>("/api/memory/explorer/projection"),
      fetchJSON<DocumentsResponse>("/api/memory/explorer/documents"),
    ])
      .then(([s, r, p, d]) => {
        setSummary(s);
        setRowsData(r);
        setProjection(p);
        setDocuments(d.documents);
      })
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  }, [offset, activeQ]);

  useLayoutEffect(() => {
    setAfterTitle(
      <Button
        type="button"
        ghost
        size="icon"
        className="text-muted-foreground hover:text-foreground"
        onClick={load}
        disabled={loading}
        aria-label={t.common.refresh}
      >
        {loading ? <Spinner /> : <RefreshCw />}
      </Button>,
    );
    setEnd(null);
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [load, loading, setAfterTitle, setEnd, t.common.refresh]);

  useEffect(() => {
    load();
  }, [load]);

  // Fetch recall floor config for the floor visualization.
  useEffect(() => {
    fetchJSON<{ memory?: { recall?: { min_score?: number } } }>("/api/config")
      .then((cfg) =>
        setRecallFloor(cfg?.memory?.recall?.min_score ?? 0.0),
      )
      .catch(() => {});
  }, []);

  // Render the scatter plot with @observablehq/plot.
  useEffect(() => {
    if (!plotRef.current || !projection || projection.points.length === 0) return;

    const points = projection.points;

    // Build nearest-neighbor ID set for highlighting when a query is placed.
    const nearestIds = queryResult
      ? new Set(queryResult.nearest.map((n) => n.id))
      : null;

    // Determine fill channel based on color mode.
    const fillFn =
      colorMode === "document"
        ? (d: ProjectionPoint) =>
            d.kind === "chunk" ? d.topic || "(none)" : "memory"
        : (d: ProjectionPoint) => d.topic || "(none)";

    const marks: any[] = [
      Plot.dot(points, {
        x: "x",
        y: "y",
        fill: fillFn,
        fillOpacity: (d: ProjectionPoint) => {
          if (nearestIds && !nearestIds.has(d.id)) return 0.15;
          if (
            filterDocId &&
            !(d.kind === "chunk" && d.topic === filterDocId)
          )
            return 0.1;
          return 0.8;
        },
        r: 4,
        tip: true,
        title: (d: ProjectionPoint) => d.label,
      }),
    ];

    // Highlight nearest neighbors when a query is placed.
    if (nearestIds && queryResult) {
      const nearestPoints = points.filter((p) => nearestIds.has(p.id));
      marks.push(
        Plot.dot(nearestPoints, {
          x: "x",
          y: "y",
          stroke: "orange",
          strokeWidth: 2,
          r: 6,
        }),
      );
    }

    // Draw the query point as a diamond.
    if (queryResult && queryResult.x !== null && queryResult.y !== null) {
      marks.push(
        Plot.dot([{ x: queryResult.x, y: queryResult.y }], {
          x: "x",
          y: "y",
          stroke: "red",
          strokeWidth: 2.5,
          r: 8,
        }),
      );
    }

    const svg = Plot.plot({
      marginBottom: 40,
      marginLeft: 50,
      marginTop: 20,
      marginRight: 20,
      color: { legend: true },
      marks,
    });

    // Click-to-select using D3's __data__ convention.
    svg.querySelectorAll("circle").forEach((circle) => {
      const data = (circle as SVGElement & { __data__?: ProjectionPoint }).__data__;
      if (data && data.id) {
        circle.style.cursor = "pointer";
        circle.addEventListener("click", () => setSelectedPoint(data));
      }
    });

    plotRef.current.innerHTML = "";
    plotRef.current.appendChild(svg);
  }, [projection, queryResult, recallFloor, colorMode, filterDocId]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setOffset(0);
    setActiveQ(searchQ);
  };

  const handleQuery = (e: React.FormEvent) => {
    e.preventDefault();
    if (!queryText.trim()) return;
    fetchJSON<{
      x: number | null;
      y: number | null;
      nearest: { id: string; score: number }[];
      degraded?: boolean;
    }>("/api/memory/explorer/projection/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: queryText }),
    })
      .then(setQueryResult)
      .catch((err) => setError(String(err)));
  };

  const hasWarning =
    summary &&
    summary.space.rows_by_model &&
    Object.keys(summary.space.rows_by_model).length > 1;

  const isEmpty =
    summary &&
    summary.totals.memories === 0 &&
    summary.totals.documents === 0 &&
    summary.totals.chunks === 0;

  return (
    <div className="flex flex-col gap-4 p-4">
      {error && (
        <Card className="border-destructive/40">
          <CardContent className="py-3 text-sm text-destructive">
            {error}
          </CardContent>
        </Card>
      )}

      {/* Header strip */}
      {summary && (
        <Stats
          items={[
            {
              label: "Model",
              value: summary.space.configured_model || "—",
            },
            {
              label: "Dimensions",
              value: summary.space.column_dim
                ? String(summary.space.column_dim)
                : "—",
            },
            {
              label: "Memories",
              value: String(summary.totals.memories),
            },
            {
              label: "Documents",
              value: String(summary.totals.documents),
            },
            {
              label: "Chunks",
              value: String(summary.totals.chunks),
            },
          ]}
        />
      )}

      {/* Warning banner */}
      {hasWarning && (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardContent className="flex items-start gap-2 py-3 text-sm">
            <AlertTriangle className="h-5 w-5 shrink-0 text-amber-500" />
            <div>
              <span className="font-medium">Mixed embedding models detected.</span>{" "}
              Rows from different models are not comparable. Run{" "}
              <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                hermes memory vectors reembed
              </code>{" "}
              to migrate all rows into the configured model's space.
            </div>
          </CardContent>
        </Card>
      )}

      {/* Projection map */}
      {projection && projection.algorithm && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-2">
                <MapIcon className="h-5 w-5 text-muted-foreground" />
                <CardTitle className="text-base">Projection map</CardTitle>
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="rounded bg-muted px-1.5 py-0.5 font-mono">
                    {projection.algorithm}
                  </span>
                  {projection.stale && (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600">
                      {projection.unprojected_count > 0
                        ? `${projection.unprojected_count} new`
                        : "stale"}
                    </span>
                  )}
                  {projection.computed_at && (
                    <span>fitted {fmtDate(projection.computed_at)}</span>
                  )}
                  {recallFloor > 0 && (
                    <span className="rounded bg-blue-500/15 px-1.5 py-0.5 text-blue-600">
                      recall floor: {recallFloor.toFixed(2)}
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {documents.length > 0 && (
                  <>
                    <button
                      type="button"
                      onClick={() => setColorMode("topic")}
                      className={`rounded px-2 py-0.5 text-xs ${
                        colorMode === "topic"
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted text-muted-foreground"
                      }`}
                    >
                      by topic
                    </button>
                    <button
                      type="button"
                      onClick={() => setColorMode("document")}
                      className={`rounded px-2 py-0.5 text-xs ${
                        colorMode === "document"
                          ? "bg-primary text-primary-foreground"
                          : "bg-muted text-muted-foreground"
                      }`}
                    >
                      by document
                    </button>
                  </>
                )}
                <span className="text-xs text-muted-foreground">
                  {projection.points.length} points
                </span>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <div ref={plotRef} className="w-full overflow-x-auto" />
            <p className="mt-2 text-xs text-muted-foreground">
              A 2-D projection of high-dimensional embeddings always distorts —
              distances are approximate, not exact.
            </p>
            {/* Query placement box */}
            <form
              onSubmit={handleQuery}
              className="mt-3 flex items-center gap-2"
            >
              <input
                type="text"
                value={queryText}
                onChange={(e) => setQueryText(e.target.value)}
                placeholder="Place a query on the map…"
                className="h-9 flex-1 rounded-md border border-border bg-background px-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
              />
              <Button type="submit" size="sm" outlined>
                Place
              </Button>
              {queryResult && (
                <Button
                  type="button"
                  size="sm"
                  ghost
                  onClick={() => {
                    setQueryResult(null);
                    setQueryText("");
                  }}
                >
                  Clear
                </Button>
              )}
            </form>
            {queryResult && (
              <div className="mt-2 text-xs text-muted-foreground">
                {queryResult.degraded ? (
                  <span className="text-amber-600">
                    UMAP model unavailable — showing nearest neighbors only.
                  </span>
                ) : queryResult.x !== null ? (
                  <span>
                    Placed at ({queryResult.x?.toFixed(1)}, {queryResult.y?.toFixed(1)})
                    {queryResult.nearest.length > 0 &&
                      ` — ${queryResult.nearest.length} nearest neighbors`}
                  </span>
                ) : (
                  <span>No coordinates available.</span>
                )}
              </div>
            )}
            {selectedPoint && (
              <div className="mt-3 rounded-md border border-border/50 bg-muted/30 px-3 py-2">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="font-mono">{shortId(selectedPoint.id)}</span>
                  <span>owner: {selectedPoint.owner_user_id}</span>
                  {selectedPoint.topic && (
                    <span>topic: {selectedPoint.topic}</span>
                  )}
                  {selectedPoint.elevated && (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-600">
                      elevated
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm">{selectedPoint.label}</p>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Document list */}
      {documents.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-4">
              <CardTitle className="text-base">
                Documents
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  {documents.length} total
                </span>
              </CardTitle>
              {filterDocId && (
                <Button
                  type="button"
                  size="sm"
                  ghost
                  onClick={() => setFilterDocId(null)}
                >
                  Clear filter
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-1">
              {documents.map((doc) => (
                <button
                  key={doc.id}
                  type="button"
                  onClick={() =>
                    setFilterDocId(
                      filterDocId === doc.id ? null : doc.id,
                    )
                  }
                  className={`flex items-center gap-3 rounded-md border px-3 py-2 text-left text-sm hover:bg-muted/30 ${
                    filterDocId === doc.id
                      ? "border-primary bg-primary/5"
                      : "border-border/50"
                  }`}
                >
                  <span className="rounded bg-muted px-2 py-0.5 text-xs font-mono">
                    {doc.source_kind}
                  </span>
                  <span className="flex-1 truncate">
                    {doc.title || doc.source_ref}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {doc.chunk_count} chunks
                  </span>
                </button>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {isEmpty && !loading && (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <Brain className="h-10 w-10 text-muted-foreground" />
            <div className="text-lg font-medium">No memories yet</div>
            <div className="text-sm text-muted-foreground max-w-md">
              The live memory tier is empty. Memories are written by the agent
              during conversations — once a session stores a fact, it will
              appear here.
            </div>
          </CardContent>
        </Card>
      )}

      {/* Search + Table */}
      {(!isEmpty || activeQ) && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-4">
              <CardTitle className="text-base">
                Memories
                {rowsData && (
                  <span className="ml-2 text-sm font-normal text-muted-foreground">
                    {rowsData.total} total
                  </span>
                )}
              </CardTitle>
              <form onSubmit={handleSearch} className="flex items-center gap-2">
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <input
                    type="text"
                    value={searchQ}
                    onChange={(e) => setSearchQ(e.target.value)}
                    placeholder="Semantic search…"
                    className="h-9 w-64 rounded-md border border-border bg-background pl-8 pr-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                </div>
                <Button type="submit" size="sm" outlined>
                  Search
                </Button>
                {activeQ && (
                  <Button
                    type="button"
                    size="sm"
                    ghost
                    onClick={() => {
                      setSearchQ("");
                      setActiveQ("");
                      setOffset(0);
                    }}
                  >
                    Clear
                  </Button>
                )}
              </form>
            </div>
          </CardHeader>
          <CardContent>
            {loading && !rowsData ? (
              <div className="flex items-center justify-center py-12">
                <Spinner />
              </div>
            ) : rowsData && rowsData.rows.length > 0 ? (
              <>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-left text-xs text-muted-foreground">
                        <th className="px-2 py-2 font-medium">ID</th>
                        <th className="px-2 py-2 font-medium">Owner</th>
                        <th className="px-2 py-2 font-medium">Vis</th>
                        <th className="px-2 py-2 font-medium">Kind</th>
                        <th className="px-2 py-2 font-medium">Topic</th>
                        <th className="px-2 py-2 font-medium">Text</th>
                        <th className="px-2 py-2 font-medium text-right">Uses</th>
                        <th className="px-2 py-2 font-medium">Last used</th>
                        {activeQ && (
                          <th className="px-2 py-2 font-medium text-right">Score</th>
                        )}
                      </tr>
                    </thead>
                    <tbody>
                      {rowsData.rows.map((row) => (
                        <tr
                          key={row.id}
                          className="border-b border-border/50 hover:bg-muted/30"
                        >
                          <td className="px-2 py-2 font-mono text-xs text-muted-foreground">
                            {shortId(row.id)}
                          </td>
                          <td className="px-2 py-2">
                            {row.owner_user_id}
                            {row.elevated && (
                              <span className="ml-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-xs text-amber-600">
                                elevated
                              </span>
                            )}
                          </td>
                          <td className="px-2 py-2 text-xs">
                            <span
                              className={
                                row.visibility === "shared"
                                  ? "rounded bg-green-500/10 px-1.5 py-0.5 text-green-600"
                                  : "rounded bg-blue-500/10 px-1.5 py-0.5 text-blue-600"
                              }
                            >
                              {row.visibility === "shared" ? "shared" : "private"}
                            </span>
                          </td>
                          <td className="px-2 py-2 text-xs">{row.kind}</td>
                          <td className="px-2 py-2 text-xs">
                            {row.topic || "—"}
                          </td>
                          <td className="px-2 py-2 max-w-md">
                            <span className="line-clamp-2 text-xs">
                              {row.text}
                            </span>
                            {row.truncated && (
                              <span className="ml-1 text-xs text-muted-foreground">
                                …
                              </span>
                            )}
                          </td>
                          <td className="px-2 py-2 text-right tabular-nums">
                            {row.uses}
                          </td>
                          <td className="px-2 py-2 text-xs text-muted-foreground">
                            {fmtDate(row.last_used)}
                          </td>
                          {activeQ && (
                            <td className="px-2 py-2 text-right tabular-nums text-xs">
                              {row.score !== null
                                ? row.score.toFixed(3)
                                : "—"}
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {/* Pagination */}
                <div className="flex items-center justify-between pt-3">
                  <span className="text-xs text-muted-foreground">
                    {rowsData.offset + 1}–
                    {Math.min(
                      rowsData.offset + rowsData.rows.length,
                      rowsData.total,
                    )}{" "}
                    of {rowsData.total}
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      size="sm"
                      outlined
                      disabled={offset === 0 || loading}
                      onClick={() =>
                        setOffset(Math.max(0, offset - PAGE_SIZE))
                      }
                    >
                      Prev
                    </Button>
                    <Button
                      size="sm"
                      outlined
                      disabled={
                        offset + PAGE_SIZE >= rowsData.total || loading
                      }
                      onClick={() => setOffset(offset + PAGE_SIZE)}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              </>
            ) : (
              <div className="py-8 text-center text-sm text-muted-foreground">
                {activeQ
                  ? `No memories matching "${activeQ}"`
                  : "No memories in this view."}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Recall use summary */}
      {summary && summary.recall_use.top.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Most-recalled memories</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col gap-2">
              {summary.recall_use.top.map((item) => (
                <div
                  key={item.id}
                  className="flex items-start gap-3 rounded-md border border-border/50 px-3 py-2"
                >
                  <span className="rounded bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                    {item.uses} uses
                  </span>
                  <span className="flex-1 text-sm">
                    {item.text}
                    {item.truncated && (
                      <span className="text-muted-foreground">…</span>
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {fmtDate(item.last_used)}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
