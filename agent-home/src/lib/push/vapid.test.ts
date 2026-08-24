import { describe, expect, it } from "vitest";

import { generateVapidKeypair } from "@/lib/push/vapid";

describe("generateVapidKeypair", () => {
  it("emits a PKCS#8 PEM private key and a 65-byte uncompressed public point", () => {
    const doc = generateVapidKeypair();
    expect(doc.private_key).toContain("-----BEGIN PRIVATE KEY-----");
    expect(doc.private_key).toContain("-----END PRIVATE KEY-----");

    // The browser's applicationServerKey is base64url of 0x04 || X || Y.
    const raw = Buffer.from(doc.public_key, "base64url");
    expect(raw.length).toBe(65);
    expect(raw[0]).toBe(0x04);
    expect(doc.created_at).toBeTruthy();
  });

  it("generates a fresh keypair each call", () => {
    const a = generateVapidKeypair();
    const b = generateVapidKeypair();
    expect(a.public_key).not.toBe(b.public_key);
  });
});
