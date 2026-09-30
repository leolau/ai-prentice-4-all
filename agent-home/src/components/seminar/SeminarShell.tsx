import type { ReactNode } from "react";

import { SEMINAR_TITLE } from "@/components/seminar/content";

/**
 * The public seminar frame: the AI&I logo, the seminar title in both
 * languages, and one readable column. Deliberately not `MobileShell` —
 * attendees have no account, so there is no app chrome to show them.
 */
export function SeminarShell({ children }: { children: ReactNode }) {
  return (
    <main
      data-component="SeminarShell"
      className="mx-auto flex min-h-dvh w-full max-w-xl flex-col gap-6 px-4 pb-12 pt-[max(1.5rem,env(safe-area-inset-top))] text-[var(--color-text)]"
    >
      <header className="flex flex-col items-center gap-3 text-center">
        {/* eslint-disable-next-line @next/next/no-img-element -- static asset, no optimiser needed */}
        <img
          src="/seminar/ai-and-i-logo.jpg"
          alt="AI&I"
          width={120}
          height={179}
          className="h-auto w-[120px] rounded-2xl"
        />
        <div>
          <h1 className="text-lg font-semibold">{SEMINAR_TITLE.zh}</h1>
          <p className="text-sm text-[var(--color-muted)]">{SEMINAR_TITLE.en}</p>
        </div>
      </header>
      {children}
    </main>
  );
}

/** A bordered card for a message such as "thank you" or "link not valid". */
export function SeminarNotice({
  component,
  children,
}: {
  component: string;
  children: ReactNode;
}) {
  return (
    <section
      data-component={component}
      className="flex flex-col gap-3 rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 text-sm leading-relaxed"
    >
      {children}
    </section>
  );
}

/** Shown once the answers are in; the slides arrive on WhatsApp. */
export function SurveyThanks() {
  return (
    <SeminarNotice component="SurveyThanks">
      <p className="text-base font-medium">多謝您完成問卷！</p>
      <p>講座簡報已經在 WhatsApp 傳送給您，請查看您與我們的對話。</p>
      <p className="text-base font-medium">Thank you for completing the survey!</p>
      <p>
        We&apos;ve sent the presentation slides to you on WhatsApp — please check
        your chat with us.
      </p>
    </SeminarNotice>
  );
}
