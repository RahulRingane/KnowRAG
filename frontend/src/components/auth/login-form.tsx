"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { resolveNextParam } from "@/components/auth/redirect";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLogin } from "@/lib/hooks/useAuth";
import { ApiError } from "@/types/api";

/** A `401` from `/auth/login` means the credentials were wrong, not that
 *  the request itself failed — `ApiError.detail` for that status is
 *  whatever FastAPI's `HTTPException` string is, which is written for logs,
 *  not this form, so it's replaced with plain copy. Every other status
 *  (`422` empty fields, `503` etc.) shows the server's own detail text. */
function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Invalid username or password.";
    return error.detail;
  }
  return "Something went wrong signing in. Please try again.";
}

/** Client island behind `(auth)/login/page.tsx`. Builds on `useLogin`
 *  (`lib/hooks/useAuth.ts`), which already stores the access token on
 *  success — this component's only job past that is redirecting to
 *  wherever the visitor was headed before the route guard sent them here. */
export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const login = useLogin();

  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    login.mutate(
      { username, password },
      {
        onSuccess: () => {
          router.replace(resolveNextParam(searchParams));
        },
      },
    );
  }

  const errorId = "login-form-error";

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
      {login.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Sign in failed</AlertTitle>
          <AlertDescription id={errorId}>{loginErrorMessage(login.error)}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="login-username">Username</Label>
        <Input
          id="login-username"
          name="username"
          autoComplete="username"
          required
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          aria-invalid={login.isError || undefined}
          aria-describedby={login.isError ? errorId : undefined}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="login-password">Password</Label>
        <Input
          id="login-password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          aria-invalid={login.isError || undefined}
          aria-describedby={login.isError ? errorId : undefined}
        />
      </div>

      <Button type="submit" disabled={login.isPending} className="mt-2">
        {login.isPending ? "Signing in…" : "Sign in"}
      </Button>

      <p className="text-muted-foreground text-center text-sm">
        Don&apos;t have an account?{" "}
        <Link href="/register" className="text-primary underline-offset-4 hover:underline">
          Register
        </Link>
      </p>
    </form>
  );
}
