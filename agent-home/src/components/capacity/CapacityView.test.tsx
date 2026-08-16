import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CapacityView } from "@/components/capacity/CapacityView";
import type { CapacityResponse } from "@/types";

function capacity(overrides: Partial<CapacityResponse> = {}): CapacityResponse {
  return {
    state: "comfortable",
    headline: "Headroom: comfortable.",
    summary: "Active conversations 3 / ~45 · memory 6.0 GB free of 16.0 GB · no write-lock waits",
    binding_constraint: null,
    bounds: [],
    recommendations: [],
    indicators: {
      active_conversations: 3,
      per_profile: { default: 2, hr: 1 },
      cap_here: 15,
      cap_box_wide: 45,
      available_mb: 6144,
      total_mb: 16384,
      hermes_rss_mb: 900,
      by_process: { gateway: 700 },
      write_lock_waits_per_hour: 0,
      write_lock_waited_s: 0,
      turn_p50_s: 2.4,
      turn_p95_s: 8.1,
      turn_samples: 120,
      profile_count: 2,
    },
    unavailable: [],
    collected_at: 1_700_000_000,
    ...overrides,
  };
}

describe("CapacityView", () => {
  it("renders the reading and the verdict", () => {
    const html = renderToStaticMarkup(<CapacityView capacity={capacity()} />);
    expect(html).toContain("Headroom: comfortable");
    expect(html).toContain("3 / ~45");
    expect(html).toContain("6.0 GB of 16.0 GB");
    expect(html).toContain("p50 2.4s");
    // Box-wide, so the per-profile split is shown when there is more than one.
    expect(html).toContain("default 2");
    expect(html).toContain("hr 1");
  });

  it("names the binding constraint when the box is not comfortable", () => {
    const html = renderToStaticMarkup(
      <CapacityView
        capacity={capacity({
          state: "watch",
          binding_constraint: {
            name: "memory",
            reason: "0.6 GB available, driven by 9 concurrent conversation(s)",
            hardware_helps: true,
          },
          recommendations: ["Retire idle profiles — hr."],
        })}
      />,
    );
    expect(html).toContain("memory");
    expect(html).toContain("9 concurrent conversation(s)");
    expect(html).toContain("Retire idle profiles");
    expect(html).not.toContain("A bigger box will not fix this one.");
  });

  it("says a bigger box will not fix a bound hardware cannot move", () => {
    const html = renderToStaticMarkup(
      <CapacityView
        capacity={capacity({
          state: "constrained",
          binding_constraint: {
            name: "write-lock waits",
            reason: "44.0 write-lock wait(s)/hour",
            hardware_helps: false,
          },
          recommendations: ["A bigger box does not fix this."],
        })}
      />,
    );
    expect(html).toContain("A bigger box will not fix this one.");
    expect(html).toContain("write-lock waits");
  });

  it("shows an unmeasured indicator as unknown, never as zero", () => {
    const html = renderToStaticMarkup(
      <CapacityView
        capacity={capacity({
          unavailable: ["memory", "write-lock waits"],
          indicators: {
            ...capacity().indicators,
            available_mb: null,
            total_mb: null,
            write_lock_waits_per_hour: null,
            turn_samples: 0,
            turn_p50_s: null,
            turn_p95_s: null,
          },
        })}
      />,
    );
    expect(html).toContain("unknown");
    expect(html).toContain("no turns yet");
    expect(html).toContain("Not measured: memory, write-lock waits");
    expect(html).not.toContain("0.0 GB");
  });

  it("shows no cap as no cap rather than inventing one", () => {
    const html = renderToStaticMarkup(
      <CapacityView
        capacity={capacity({
          indicators: { ...capacity().indicators, cap_box_wide: null },
        })}
      />,
    );
    expect(html).toContain("3 (no cap set)");
  });
});
