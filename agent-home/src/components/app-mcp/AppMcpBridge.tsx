"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

import { reportState, startBridge } from "@/lib/app-mcp/bridge";
import { registerSoftNavigate } from "@/lib/app-mcp/navigation";

/**
 * app-mcp presence in the shell: starts the bridge once, registers the
 * Next.js router so the `navigate` command can soft-navigate (a full
 * reload would sever the WebSocket this very channel runs on), and
 * re-reports the UI context on every route change, so the agent's
 * awareness ("which page, which element") tracks navigation without any
 * page code knowing about it. Renders nothing.
 */
export function AppMcpBridge() {
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    startBridge();
  }, []);

  useEffect(() => {
    registerSoftNavigate((path) => router.push(path));
    return () => registerSoftNavigate(null);
  }, [router]);

  useEffect(() => {
    reportState();
  }, [pathname]);

  return null;
}
