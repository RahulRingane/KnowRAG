"use client";

import * as React from "react";
import Link from "next/link";
import { CircleCheck, Lock } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useRegister } from "@/lib/hooks/useAuth";
import { ApiError } from "@/types/api";

/** Stated as a hint only, never enforced client-side: the backend is
 *  concurrently gaining an 8-character minimum, and a client-side copy of
 *  that number would drift the moment the server's policy changes — it
 *  would then reject passwords the server accepts, or worse, accept ones
 *  the server doesn't. The `422` detail text below is the only thing that
 *  actually decides pass/fail. */
const PASSWORD_HINT = "At least 8 characters.";

/** Client island behind `(auth)/register/page.tsx`. Three outcomes beyond
 *  the plain form, each rendered as its own state rather than folded into
 *  one generic error banner: success, the `403` "signup is closed" case
 *  (expected and unalarming — this instance already has an owner), and
 *  everything else (network errors, `422` validation detail from the
 *  server, etc.). */
export function RegisterForm() {
  const register = useRegister();
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");

  function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    register.mutate({ username, password });
  }

  if (register.isSuccess) {
    return (
      <Alert role="status">
        <CircleCheck />
        <AlertTitle>Account created</AlertTitle>
        <AlertDescription>
          You can now{" "}
          <Link href="/login" className="text-primary underline-offset-4 hover:underline">
            sign in
          </Link>
          .
        </AlertDescription>
      </Alert>
    );
  }

  const error = register.error;
  const isClosed = error instanceof ApiError && error.status === 403;

  if (isClosed) {
    return (
      <Alert role="status">
        <Lock />
        <AlertTitle>Registration is closed</AlertTitle>
        <AlertDescription>
          This instance already has an account.{" "}
          <Link href="/login" className="text-primary underline-offset-4 hover:underline">
            Sign in
          </Link>{" "}
          instead.
        </AlertDescription>
      </Alert>
    );
  }

  // Any other failure: network error, or a `422` whose `detail` is the
  // server's own validation message (e.g. the password-length rule) —
  // shown verbatim rather than replaced with client copy, per the plan:
  // the server's message is the source of truth.
  const errorMessage = error
    ? error instanceof ApiError
      ? error.detail
      : "Something went wrong creating your account. Please try again."
    : null;
  const errorId = "register-form-error";

  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-4">
      {errorMessage ? (
        <Alert variant="destructive">
          <AlertTitle>Registration failed</AlertTitle>
          <AlertDescription id={errorId}>{errorMessage}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="register-username">Username</Label>
        <Input
          id="register-username"
          name="username"
          autoComplete="username"
          required
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          aria-invalid={!!errorMessage || undefined}
          aria-describedby={errorMessage ? errorId : undefined}
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="register-password">Password</Label>
        <Input
          id="register-password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          aria-invalid={!!errorMessage || undefined}
          aria-describedby={
            errorMessage ? `${errorId} register-password-hint` : "register-password-hint"
          }
        />
        <p id="register-password-hint" className="text-muted-foreground text-xs">
          {PASSWORD_HINT}
        </p>
      </div>

      <Button type="submit" disabled={register.isPending} className="mt-2">
        {register.isPending ? "Creating account…" : "Create account"}
      </Button>

      <p className="text-muted-foreground text-center text-sm">
        Already have an account?{" "}
        <Link href="/login" className="text-primary underline-offset-4 hover:underline">
          Sign in
        </Link>
      </p>
    </form>
  );
}
