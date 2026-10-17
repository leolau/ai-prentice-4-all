import { describe, expect, it } from "vitest";

import {
  displaySmbPath,
  normalizeSmbPath,
  parseNetworkFolderBody,
  smbServer,
} from "@/lib/folder-bridge/networkFolders";

describe("normalizeSmbPath", () => {
  it.each([
    ["\\\\fs01.office.local\\Projects\\Tenders", "//fs01.office.local/Projects/Tenders"],
    ["smb://fs01.office.local/Projects/Tenders/", "//fs01.office.local/Projects/Tenders"],
    ["SMB://10.20.1.5/Shared", "//10.20.1.5/Shared"],
    ["//fs01/Shared//2026\\Q3", "//fs01/Shared/2026/Q3"],
    ["  \\\\fs01\\HR Policies  ", "//fs01/HR Policies"],
  ])("normalises %j", (text, path) => {
    expect(normalizeSmbPath(text)).toEqual({ path, error: null });
  });

  it.each([
    ["", /Enter the SMB path/],
    ["fs01\\Shared", /must look like/],
    ["C:\\Users\\leo", /must look like/],
    ["https://fs01/Shared", /must look like/],
    ["\\\\fs01", /both the server and the share/],
    ["\\\\fs01\\", /both the server and the share/],
    ["smb://leo@fs01/Shared", /login out of the path/],
    ["\\\\fs 01\\Shared", /not a valid file server/],
    ["\\\\fs01:445\\Shared", /not a valid file server/],
    ["\\\\fs01\\Shared\\..\\Secret", /\. or \.\./],
    ["\\\\fs01\\Sha*red", /not a valid share/],
    ["\\\\fs01\\Shared\u0000x", /invalid characters/],
    ["\\\\fs01\\Shared\nx", /invalid characters/],
  ])("rejects %j", (text, msg) => {
    const { path, error } = normalizeSmbPath(text);
    expect(path).toBe("");
    expect(error).toMatch(msg);
  });

  it("round-trips to the Windows form", () => {
    expect(displaySmbPath("//fs01.office.local/Projects/Tenders")).toBe(
      "\\\\fs01.office.local\\Projects\\Tenders",
    );
    expect(smbServer("smb://fs01/Shared")).toBe("fs01");
    expect(smbServer("nope")).toBeNull();
  });
});

describe("parseNetworkFolderBody", () => {
  const base = { name: " Tenders ", vpn: "Office files", share_path: "//fs01/x" };

  it("trims, whitelists and drops an empty password", () => {
    expect(
      parseNetworkFolderBody({ ...base, share_password: "", share_username: "", extra: 1 }),
    ).toEqual({
      input: { name: "Tenders", vpn: "Office files", share_path: "//fs01/x", share_username: null },
      error: null,
    });
  });

  it("keeps a password verbatim (no trimming)", () => {
    const parsed = parseNetworkFolderBody({ ...base, share_password: " p w " });
    expect(parsed.input?.share_password).toBe(" p w ");
  });

  it.each([null, "x", [], { ...base, name: 1 }, { ...base, share_domain: false }])(
    "rejects %j",
    (body) => {
      expect(parseNetworkFolderBody(body).error).toBeTruthy();
    },
  );
});
