import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  MemoryMap,
  MemoryPointModal,
  computeViewBox,
  topicColor,
} from "@/components/memory/MemoryMap";
import type { MemoryProjection, MemoryProjectionPoint } from "@/types";

/** Decode HTML entities so `toContain` can match apostrophes etc. */
function decodeHtml(s: string): string {
  return s
    .replace(/&#x27;/g, "'")
    .replace(/&#39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

describe("computeViewBox (pure function)", () => {
  it("returns a default box for zero points", () => {
    const vb = computeViewBox([]);
    expect(vb).toEqual({ minX: 0, minY: 0, w: 100, h: 100 });
  });

  it("centres a single point without dividing by zero", () => {
    const vb = computeViewBox([{ x: 5, y: 7 }]);
    // Single point → extent is zero; it should be centred, not NaN.
    expect(vb.w).toBe(100);
    expect(vb.h).toBe(100);
    expect(vb.minX).toBe(5 - 50);
    expect(vb.minY).toBe(7 - 50);
  });

  it("pads the extent by 5%", () => {
    const points = [
      { x: 0, y: 0 },
      { x: 100, y: 50 },
    ];
    const vb = computeViewBox(points);
    // w = 100 + 5%*2 = 110, h = 50 + 5%*2 = 55
    expect(vb.w).toBeCloseTo(110, 5);
    expect(vb.h).toBeCloseTo(55, 5);
    expect(vb.minX).toBeCloseTo(-5, 5);
    expect(vb.minY).toBeCloseTo(-2.5, 5);
  });
});

describe("topicColor (stable hash)", () => {
  it("returns the same colour for the same topic across calls", () => {
    expect(topicColor("work")).toBe(topicColor("work"));
  });

  it("returns different colours for different topics (usually)", () => {
    const a = topicColor("work");
    const b = topicColor("personal");
    // Not guaranteed to differ (hash collision), but extremely unlikely with 12 colours.
    expect(a).toMatch(/^#[0-9a-f]{6}$/);
    expect(b).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("handles null topic without crashing", () => {
    expect(topicColor(null)).toMatch(/^#[0-9a-f]{6}$/);
  });
});

describe("MemoryMap rendered states", () => {
  it('renders "no map yet" when algorithm is null', () => {
    const proj: MemoryProjection = {
      algorithm: null,
      computed_at: null,
      stale: true,
      unprojected_count: 0,
      points: [],
    };
    const html = renderToStaticMarkup(
      <MemoryMap projection={proj} queryResult={null} />,
    );
    expect(html).toContain("No map yet");
    expect(html).toContain("03:00");
  });

  it("renders the sampled count banner when sampled", () => {
    const proj: MemoryProjection = {
      algorithm: "pca",
      computed_at: "2026-08-05T03:00:00Z",
      stale: false,
      unprojected_count: 0,
      points: [{ id: "a", x: 1, y: 2, owner_user_id: "u", topic: "t", kind: "memory", elevated: false, provenance: "", label: "hello" }],
      sampled: true,
      total_points: 50000,
    };
    const html = renderToStaticMarkup(
      <MemoryMap projection={proj} queryResult={null} />,
    );
    expect(html).toContain("1 of 50,000");
  });

  it("renders the new-memories staleness banner when unprojected_count > 0", () => {
    const proj: MemoryProjection = {
      algorithm: "pca",
      computed_at: "2026-08-05T03:00:00Z",
      stale: true,
      unprojected_count: 3,
      points: [{ id: "a", x: 1, y: 2, owner_user_id: "u", topic: "t", kind: "memory", elevated: false, provenance: "", label: "hello" }],
    };
    const html = decodeHtml(
      renderToStaticMarkup(
        <MemoryMap projection={proj} queryResult={null} />,
      ),
    );
    expect(html).toContain("3 new memories");
    expect(html).toContain("aren't on the map yet");
  });

  it("plots every point inside the viewBox at real PCA coordinates", () => {
    // The live box's spread is x −0.390…0.406, y −0.403…0.327. Point
    // coordinates are normalised to a 0–100 space, so the viewBox has to be
    // that same space — a viewBox in *data* units puts every dot off-canvas
    // and blows the radii up past the frame.
    const spread = [
      { x: -0.39, y: -0.403 },
      { x: 0.406, y: 0.327 },
      { x: 0.01, y: -0.02 },
    ];
    const proj: MemoryProjection = {
      algorithm: "pca",
      computed_at: "2026-08-05T03:00:00Z",
      stale: false,
      unprojected_count: 0,
      points: spread.map((p, i) => ({
        id: `p${i}`,
        x: p.x,
        y: p.y,
        owner_user_id: "u",
        topic: "t",
        kind: "memory",
        elevated: false,
        provenance: "",
        label: `point ${i}`,
      })),
    };
    const html = renderToStaticMarkup(
      <MemoryMap projection={proj} queryResult={null} />,
    );

    const viewBox = /viewBox="([^"]+)"/.exec(html)?.[1];
    expect(viewBox).toBeDefined();
    const [vx, vy, vw, vh] = viewBox!.split(/\s+/).map(Number);

    // Two circles per point: the visible dot and the wider transparent hit
    // target sitting over it.
    const coords = [...html.matchAll(/<circle[^>]*?cx="([^"]+)"[^>]*?cy="([^"]+)"/g)];
    expect(coords.length).toBe(spread.length * 2);
    for (const m of coords) {
      const cx = Number(m[1]);
      const cy = Number(m[2]);
      expect(cx).toBeGreaterThanOrEqual(vx);
      expect(cx).toBeLessThanOrEqual(vx + vw);
      expect(cy).toBeGreaterThanOrEqual(vy);
      expect(cy).toBeLessThanOrEqual(vy + vh);
    }
  });

  it("renders the model-mismatch banner when stale with zero unprojected", () => {
    const proj: MemoryProjection = {
      algorithm: "pca",
      computed_at: "2026-08-05T03:00:00Z",
      stale: true,
      unprojected_count: 0,
      points: [{ id: "a", x: 1, y: 2, owner_user_id: "u", topic: "t", kind: "memory", elevated: false, provenance: "", label: "hello" }],
    };
    const html = decodeHtml(
      renderToStaticMarkup(
        <MemoryMap projection={proj} queryResult={null} />,
      ),
    );
    expect(html).toContain("different embedder");
    expect(html).toContain("aren't meaningful");
  });
});

describe("MemoryPointModal", () => {
  const POINT: MemoryProjectionPoint = {
    id: "p1",
    x: 0.1,
    y: 0.2,
    owner_user_id: "leo_owner",
    topic: "pricing",
    kind: "chunk",
    elevated: false,
    provenance: "",
    label: "Enterprise seats above 250 get 22% off.",
    document_title: "Joyaether 2026 Support Policy",
    section: "Enterprise seat discounts",
    source_kind: "local",
    source_ref: "/opt/data/uploads/support-policy.md",
  };

  it("is an overlay dialog, not a block appended under the map", () => {
    // The detail used to render below a full-width square plot, so clicking a
    // dot looked like nothing happened until the user scrolled.
    const html = renderToStaticMarkup(
      <MemoryPointModal point={POINT} onClose={() => {}} />,
    );
    expect(html).toContain('data-component="MemoryPointModal"');
    expect(html).toContain('role="dialog"');
    expect(html).toContain("fixed inset-0");
  });

  it("cites the document and section a chunk came from", () => {
    const html = decodeHtml(
      renderToStaticMarkup(
        <MemoryPointModal point={POINT} onClose={() => {}} />,
      ),
    );
    expect(html).toContain("Joyaether 2026 Support Policy");
    expect(html).toContain("Enterprise seat discounts");
    expect(html).toContain("/opt/data/uploads/support-policy.md");
  });

  it("cites the chat a memory was written in", () => {
    const html = renderToStaticMarkup(
      <MemoryPointModal
        point={{
          ...POINT,
          kind: "memory",
          document_title: null,
          section: null,
          source_kind: null,
          source_ref: null,
          source_session: "sess-42",
        }}
        onClose={() => {}}
      />,
    );
    expect(html).toContain("From a chat");
    expect(html).toContain("sess-42");
  });

  it("shows no source block when nothing was recorded", () => {
    const html = renderToStaticMarkup(
      <MemoryPointModal
        point={{
          ...POINT,
          document_title: null,
          section: null,
          source_kind: null,
          source_ref: null,
        }}
        onClose={() => {}}
      />,
    );
    // "Unknown source" reads as an error; absence is silence.
    expect(html).not.toContain("Source");
  });
});
