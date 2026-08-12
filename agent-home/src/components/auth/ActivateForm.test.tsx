import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ActivateForm } from "@/components/auth/ActivateForm";
import { ResetRequestForm } from "@/components/auth/ResetRequestForm";

const TOKEN = "s3cr3t-raw-token-value";

describe("ActivateForm", () => {
  it("asks for a password twice and states the 12-character floor", () => {
    const html = renderToStaticMarkup(<ActivateForm token={TOKEN} />);
    expect(html).toContain('data-component="ActivateForm"');
    expect(html).toContain("12");
    // Two password fields: the new password and its confirmation.
    expect(html.match(/type="password"/g)).toHaveLength(2);
  });

  it("never renders the raw token back into the page", () => {
    const html = renderToStaticMarkup(<ActivateForm token={TOKEN} />);
    // The token is a live credential; echoing it into markup would leak it into
    // any HTML cache, screenshot or bug report of this page.
    expect(html).not.toContain(TOKEN);
  });
});

describe("ResetRequestForm", () => {
  it("collapses behind a prompt and asks only for an email", () => {
    const html = renderToStaticMarkup(<ResetRequestForm />);
    expect(html).toContain('data-component="ResetRequestToggle"');
    expect(html).not.toContain('type="password"');
  });
});
