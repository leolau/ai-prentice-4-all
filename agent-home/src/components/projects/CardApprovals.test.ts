import { describe, expect, it } from "vitest";

import { describeApproval } from "@/components/projects/CardApprovals";

describe("describeApproval", () => {
  it("splits a recorded MCP call into its intent, arguments and a link to the design", () => {
    const view = describeApproval({
      key: "mcp_canva_export_design",
      label: "mcp_canva_export_design",
      detail:
        'mcp_canva_export_design\n{"design_id": "DAHXSa-9nsc", "format": {"type": "pdf", "export_quality": "pro"}, "user_intent": "Export the final deck to a PDF"}',
      requested_at: 1,
    });
    expect(view.tool).toBe("mcp_canva_export_design");
    expect(view.intent).toBe("Export the final deck to a PDF");
    expect(view.args.map(([name]) => name)).toEqual(["design_id", "format"]);
    expect(view.args[0][1]).toBe("DAHXSa-9nsc");
    expect(JSON.parse(view.args[1][1])).toEqual({ type: "pdf", export_quality: "pro" });
    expect(view.text).toBeNull();
    expect(view.links).toEqual([
      { label: "Open the design in Canva", href: "https://www.canva.com/design/DAHXSa-9nsc/view" },
    ]);
  });

  it("keeps a command as text, and links any URL in it", () => {
    const command = "curl -o out.pdf https://example.com/export/123 && ls -la out.pdf";
    const view = describeApproval({
      key: "script execution via -e/-c flag",
      label: "script execution via -e/-c flag",
      detail: command,
      requested_at: 1,
    });
    expect(view.tool).toBeNull();
    expect(view.args).toEqual([]);
    expect(view.text).toBe(command);
    expect(view.links.map((l) => l.href)).toEqual(["https://example.com/export/123"]);
  });

  it("reads bare JSON arguments and shows nothing it doesn't have", () => {
    expect(
      describeApproval({ key: "mcp_canva_create_upload_url", label: "x", detail: '{"name": "logo.png"}', requested_at: 1 }),
    ).toMatchObject({ tool: "mcp_canva_create_upload_url", args: [["name", "logo.png"]], links: [] });
    expect(describeApproval({ key: "k", label: "k", detail: null, requested_at: 1 })).toEqual({
      tool: null,
      intent: null,
      args: [],
      text: null,
      links: [],
    });
  });
});
