import type { Metadata } from "next";

import { RegisterForm } from "@/components/auth/register-form";

export const metadata: Metadata = { title: "Register" };

/**
 * No `Suspense` needed here unlike the login page — `RegisterForm` doesn't
 * read search params (registration has no `?next=` to preserve; a fresh
 * account still has to sign in separately, since `POST /auth/register`
 * returns an `AuthUser`, not a token pair).
 */
export default function RegisterPage() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1 text-center">
        <h1 className="text-xl font-semibold">Create an account</h1>
        <p className="text-muted-foreground text-sm">
          Registration is available only until this instance has its first account.
        </p>
      </div>
      <RegisterForm />
    </div>
  );
}
