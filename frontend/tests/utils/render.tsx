import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, RenderOptions } from "@testing-library/react";
import { vi } from "vitest";

/**
 * Mock implementations of Next.js navigation hooks.
 * Tests can override these via `vi.mocked()` per-test.
 */
export const mockRouter = {
  push: vi.fn(),
  replace: vi.fn(),
  back: vi.fn(),
  forward: vi.fn(),
  refresh: vi.fn(),
  prefetch: vi.fn(),
};

export const mockPathname = "/";
export const mockSearchParams = new URLSearchParams();

/**
 * Renders a component wrapped in TanStack Query provider with retry disabled.
 * Automatically mocks next/navigation hooks (useRouter, usePathname,
 * useSearchParams) so components that depend on them work in tests.
 *
 * Usage:
 *   const { getByRole } = renderWithProviders(<LoginForm />);
 *   expect(mockRouter.replace).toHaveBeenCalledWith("/home");
 */
export function renderWithProviders(
  ui: React.ReactElement,
  options?: Omit<RenderOptions, "wrapper">
) {
  // Create a fresh QueryClient for each test with retry disabled
  // so failures surface immediately instead of hanging on retry attempts
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
      mutations: {
        retry: false,
      },
    },
  });

  function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    );
  }

  return render(ui, { wrapper: Wrapper, ...options });
}
