import type { Metadata } from "next";
import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";
import { TextSkeleton } from "@/components/layout/skeletons";

export const metadata: Metadata = { title: "Sign in" };

/**
 * `LoginForm` reads `useSearchParams()` (for `?next=`), which opts a
 * component out of static rendering unless it's wrapped in `Suspense` —
 * without this, `next build` fails prerendering this route rather than
 * just warning. The fallback is a skeleton, not a spinner, per §5.3; it's
 * only visible for the one tick before the client component hydrates.
 */
export default function LoginPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1 text-center">
        <h1 className="text-xl font-semibold">Sign in</h1>
        <p className="text-muted-foreground text-sm">
          Sign in to ask questions and fact-check statements against your ingested documents.
        </p>
      </div>
      <Suspense fallback={<TextSkeleton lines={4} />}>
        <LoginForm />
      </Suspense>
    </div>
  );
}
