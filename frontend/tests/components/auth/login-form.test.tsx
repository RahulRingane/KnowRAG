import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LoginForm } from "@/components/auth/login-form";
import { clearAccessToken } from "@/lib/auth/token-store";
import { renderWithProviders } from "../../utils/render";
import { server } from "../../setup";
import * as nextNav from "next/navigation";

const API_BASE_URL = "http://localhost:8000";

vi.mock("next/navigation");

describe("LoginForm", () => {
  let mockReplace: ReturnType<typeof vi.fn>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let mockRouter: any;

  beforeEach(() => {
    clearAccessToken();
    mockReplace = vi.fn();
    mockRouter = {
      replace: mockReplace,
      push: vi.fn(),
      back: vi.fn(),
      forward: vi.fn(),
      refresh: vi.fn(),
      prefetch: vi.fn(),
    };

    vi.mocked(nextNav.useRouter).mockReturnValue(mockRouter);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    vi.mocked(nextNav.useSearchParams).mockReturnValue(new URLSearchParams() as any);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  describe("successful login", () => {
    it("stores access token on successful login", async () => {
      const user = userEvent.setup();

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      await waitFor(() => {
        expect(mockReplace).toHaveBeenCalledWith("/");
      });
    });

    it("redirects to home on successful login without next param", async () => {
      const user = userEvent.setup();

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      await waitFor(() => {
        expect(mockReplace).toHaveBeenCalledWith("/");
      });
    });

    it("redirects to next param if provided", async () => {
      const user = userEvent.setup();

      vi.mocked(nextNav.useSearchParams).mockReturnValue(
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        new URLSearchParams("next=%2Fdocuments") as any
      );

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      await waitFor(() => {
        expect(mockReplace).toHaveBeenCalledWith("/documents");
      });
    });

    it("disables submit button while pending", async () => {
      const user = userEvent.setup();

      // Use a slow handler to keep request pending
      server.use(
        http.post(`${API_BASE_URL}/auth/login`, async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json({
            access_token: "test-token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      // Button should be disabled immediately
      expect(submitButton).toBeDisabled();

      // Eventually the request completes and button is re-enabled
      await waitFor(() => {
        expect(submitButton).not.toBeDisabled();
      });
    });
  });

  describe("login failure", () => {
    it("renders 401 as user-friendly message, not server detail", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/login`, () => {
          return HttpResponse.json(
            { detail: "Invalid credentials (this is for logs)" },
            { status: 401 }
          );
        })
      );

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "wrongpassword");
      await user.click(submitButton);

      // Should see the user-friendly message, not the server one
      const errorMessage = await screen.findByText("Invalid username or password.");
      expect(errorMessage).toBeInTheDocument();
    });

    it("renders 422 validation detail from server as-is", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/login`, () => {
          return HttpResponse.json(
            { detail: "Invalid credentials format" },
            { status: 422 }
          );
        })
      );

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "pass");
      await user.click(submitButton);

      const errorMessage = await screen.findByText("Invalid credentials format");
      expect(errorMessage).toBeInTheDocument();
    });

    it("renders 503 service error detail", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/login`, () => {
          return HttpResponse.json(
            { detail: "Service temporarily unavailable" },
            { status: 503 }
          );
        })
      );

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      const errorMessage = await screen.findByText("Service temporarily unavailable");
      expect(errorMessage).toBeInTheDocument();
    });
  });

  describe("accessibility", () => {
    it("labels input fields correctly", () => {
      renderWithProviders(<LoginForm />);

      expect(screen.getByLabelText("Username")).toBeInTheDocument();
      expect(screen.getByLabelText("Password")).toBeInTheDocument();
    });

    it("associates error message with input fields via aria-describedby", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/login`, () => {
          return HttpResponse.json(
            { detail: "Invalid username or password" },
            { status: 401 }
          );
        })
      );

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      // Before error, aria-describedby should not be set
      expect(usernameInput).not.toHaveAttribute("aria-describedby");
      expect(passwordInput).not.toHaveAttribute("aria-describedby");

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "wrongpass");
      await user.click(submitButton);

      // After error, both should have aria-describedby pointing to error
      await waitFor(() => {
        expect(usernameInput).toHaveAttribute("aria-describedby", "login-form-error");
        expect(passwordInput).toHaveAttribute("aria-describedby", "login-form-error");
      });
    });

    it("marks inputs as invalid during error via aria-invalid", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/login`, () => {
          return HttpResponse.json({ detail: "Login failed" }, { status: 401 });
        })
      );

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button", { name: /sign in/i });

      // Before error, aria-invalid should not be set
      expect(usernameInput).not.toHaveAttribute("aria-invalid");
      expect(passwordInput).not.toHaveAttribute("aria-invalid");

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "wrongpass");
      await user.click(submitButton);

      // After error, both should be marked invalid
      await waitFor(() => {
        expect(usernameInput).toHaveAttribute("aria-invalid", "true");
        expect(passwordInput).toHaveAttribute("aria-invalid", "true");
      });
    });

    it("shows pending state to screen readers", async () => {
      const user = userEvent.setup();

      server.use(
        http.post(`${API_BASE_URL}/auth/login`, async () => {
          await new Promise((resolve) => setTimeout(resolve, 100));
          return HttpResponse.json({
            access_token: "test-token",
            token_type: "bearer",
            expires_in: 900,
          });
        })
      );

      renderWithProviders(<LoginForm />);

      const usernameInput = screen.getByLabelText("Username");
      const passwordInput = screen.getByLabelText("Password");
      const submitButton = screen.getByRole("button");

      await user.type(usernameInput, "testuser");
      await user.type(passwordInput, "password123");
      await user.click(submitButton);

      // Button text should change to indicate pending
      expect(submitButton).toHaveTextContent("Signing in…");
    });
  });

  describe("registration link", () => {
    it("provides link to register page", () => {
      renderWithProviders(<LoginForm />);

      const registerLink = screen.getByRole("link", { name: /register/i });
      expect(registerLink).toHaveAttribute("href", "/register");
    });
  });
});
