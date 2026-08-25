import { createPrivateKey } from "node:crypto";

import { describe, expect, it } from "vitest";

import { generateVapidKeypair } from "@/lib/push/vapid";

describe("generateVapidKeypair", () => {
  it("emits a b64url PKCS#8 DER private key and a 65-byte uncompressed public point", () => {
    const doc = generateVapidKeypair();
    // py_vapid parses RAW/DER only — the stored key must be base64url DER.
    expect(doc.private_key).not.toContain("-----BEGIN");
    const der = Buffer.from(doc.private_key, "base64url");
    expect(der.length).toBeGreaterThan(100);
    const key = createPrivateKey({ key: der, format: "der", type: "pkcs8" });
    expect(key.asymmetricKeyType).toBe("ec");

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
