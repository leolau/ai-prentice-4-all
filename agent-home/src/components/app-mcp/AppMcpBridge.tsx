"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";

import { reportState, startBridge } from "@/lib/app-mcp/bridge";

/**
 * app-mcp presence in the shell: starts the bridge once and re-reports the
 * UI context on every route change, so the agent's awareness ("which page,
 * which element") tracks navigation without any page code knowing about it.
 * Renders nothing.
 */
export function AppMcpBridge() {
  const pathname = usePathname();

  useEffect(() => {
    startBridge();
  }, []);

  useEffect(() => {
    reportState();
  }, [pathname]);

  return null;
}
