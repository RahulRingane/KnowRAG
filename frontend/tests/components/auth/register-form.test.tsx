import { beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { RegisterForm } from "@/components/auth/register-form";
import { clearAccessToken } from "@/lib/auth/token-store";
import { renderWithProviders } from "../../utils/render";
import { server } from "../../setup";

const API_BASE_URL = "http://localhost:8000";

describe("RegisterForm", () => {
  beforeEach(() => {
    clearAccessToken();
  });

  describe("successful registration", () => {
    it("renders success message after successful registration", async () => {
      const user = userEvent.setup();

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /create account/i });

      await user.type(usernameInput, "newuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      // Should show success state
      const successTitle = await screen.findByText("Account created");
      expect(successTitle).toBeInTheDocument();

      // Should show link to sign in
      const signInLink = screen.getByRole("link", { name: /sign in/i });
      expect(signInLink).toHaveAttribute("href", "/login");
    });

    it("disables submit button while pending", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/register`, async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json({
            id: 1,
            username: "newuser",
            created_at: "2026-08-11T00:00:00Z",
          });
        })
      );

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /create account/i });

      await user.type(usernameInput, "newuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      // Button should be disabled immediately
      expect(submitButton).toBeDisabled();

      // Eventually completes
      await waitFor(() => {
        expect(screen.getByText("Account created")).toBeInTheDocument();
      });
    });
  });

  describe("registration closed (403)", () => {
    it("renders dedicated 'registration is closed' state on 403", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/register`, () => {
          return HttpResponse.json(
            { detail: "Registration is closed" },
            { status: 403 }
          );
        })
      );

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /create account/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      // Should show the dedicated closed state with lock icon
      const closedTitle = await screen.findByText("Registration is closed");
      expect(closedTitle).toBeInTheDocument();

      // Should offer a sign in link instead
      const signInLink = screen.getByRole("link", { name: /sign in/i });
      expect(signInLink).toHaveAttribute("href", "/login");
    });

    it("renders message that 'This instance already has an account'", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/register`, () => {
          return HttpResponse.json({ detail: "User already exists" }, { status: 403 });
        })
      );

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /create account/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      const message = await screen.findByText(
        /This instance already has an account/
      );
      expect(message).toBeInTheDocument();
    });
  });

  describe("validation errors (422)", () => {
    it("renders server's 422 validation detail as-is", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/register`, () => {
          return HttpResponse.json(
            { detail: "Password must be at least 8 characters" },
            { status: 422 }
          );
        })
      );

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /create account/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "short");
      await user.click(submitButton);

      const errorMessage = await screen.findByText(
        "Password must be at least 8 characters"
      );
      expect(errorMessage).toBeInTheDocument();
    });

    it("does not enforce password policy client-side", () => {
      // The PASSWORD_HINT is shown as informational only,
      // not as validation, so the form should accept any input
      renderWithProviders(<RegisterForm />);

      const passwordInput = screen.getByLabelText("Password");
      expect(passwordInput).not.toHaveAttribute("minLength");
    });

    it("shows password hint text", () => {
      renderWithProviders(<RegisterForm />);

      // The hint should be visible and reference the 8-character rule
      const hint = screen.getByText(/At least 8 characters/);
      expect(hint).toBeInTheDocument();
    });
  });

  describe("other errors", () => {
    it("renders network/500 error with fallback message", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/register`, () => {
          return HttpResponse.json(
            { detail: "Internal server error" },
            { status: 500 }
          );
        })
      );

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /create account/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      const errorMessage = await screen.findByText("Internal server error");
      expect(errorMessage).toBeInTheDocument();
    });
  });

  describe("accessibility", () => {
    it("labels input fields correctly", () => {
      renderWithProviders(<RegisterForm />);

      expect(screen.getByLabelText("Username")).toBeInTheDocument();
      expect(screen.getByLabelText("Password")).toBeInTheDocument();
    });

    it("associates password field with hint via aria-describedby", () => {
      renderWithProviders(<RegisterForm />);

      const passwordInput = screen.getByLabelText("Password");
      expect(passwordInput).toHaveAttribute(
        "aria-describedby",
        "register-password-hint"
      );
    });

    it("associates password field with error when present", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/register`, () => {
          return HttpResponse.json(
            { detail: "Password too short" },
            { status: 422 }
          );
        })
      );

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /create account/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "pass");
      await user.click(submitButton);

      // After error, should have both error and hint in aria-describedby
      await waitFor(() => {
        const describedBy = passwordInput.getAttribute("aria-describedby");
        expect(describedBy).toContain("register-form-error");
        expect(describedBy).toContain("register-password-hint");
      });
    });

    it("marks inputs as invalid when error occurs", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/register`, () => {
          return HttpResponse.json({ detail: "Error" }, { status: 422 });
        })
      );

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /create account/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      await waitFor(() => {
        expect(usernameInput).toHaveAttribute("aria-invalid", "true");
        expect(passwordInput).toHaveAttribute("aria-invalid", "true");
      });
    });

    it("shows pending state in button text", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/register`, async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json({
            id: 1,
            username: "newuser",
            created_at: "2026-08-11T00:00:00Z",
          });
        })
      );

      renderWithProviders(<RegisterForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button");

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      expect(submitButton).toHaveTextContent("Creating account…");
    });
  });

  describe("sign in link", () => {
    it("provides link to sign in page", () => {
      renderWithProviders(<RegisterForm />);

      const signInLink = screen.getByRole("link", { name: /sign in/i });
      expect(signInLink).toHaveAttribute("href", "/login");
    });
  });
});
