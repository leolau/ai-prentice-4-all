import { redirect } from "next/navigation";

import { ResetRequestForm } from "@/components/auth/ResetRequestForm";
import { LoginForm, type ProviderOption } from "@/components/LoginForm";
import { MobileShell } from "@/components/MobileShell";
import { HermesApiClient, HermesApiError } from "@/lib/api/client";
import { getPrincipal } from "@/lib/auth/principal";

export const dynamic = "force-dynamic";

/** Login page for the C1 bridge; already-authenticated sessions skip to `/`. */
export default async function LoginPage() {
  if (await getPrincipal()) {
    redirect("/");
  }

  let providers: ProviderOption[] = [];
  try {
    const res = await new HermesApiClient().authProviders();
    providers = res.providers;
  } catch (err) {
    // Login is still usable if the AI layer exposes a default password
    // provider; surface nothing here beyond an empty list.
    if (!(err instanceof HermesApiError)) {
      providers = [];
    }
  }

  return (
    <MobileShell title="Sign in" showCoral={false}>
      <p className="mb-6 text-sm text-[var(--color-muted)]">
        Sign in with your Hermes account to open your agent home.
      </p>
      <LoginForm providers={providers} />
      {/* Self-service recovery: a locked-out user cannot sign in to ask, and
       * nobody hands out temporary passwords by hand any more. */}
      <div className="mt-4">
        <ResetRequestForm />
      </div>
    </MobileShell>
  );
}
