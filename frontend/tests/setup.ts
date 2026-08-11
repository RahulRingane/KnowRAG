import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";
import { setupServer } from "msw/node";

import { defaultHandlers } from "./mocks/handlers";

// Shared Vitest setup, loaded before every test file (see vitest.config.ts
// `setupFiles`). Keep this file config-only — no test-suite-specific mocks
// here; those belong in the tests that need them (or a shared `tests/mocks/`
// helper if multiple suites converge on the same fixture).

const server = setupServer(...defaultHandlers);

// Stub scrollIntoView for jsdom (not implemented).
// Tests that need to verify scrolling behaviour should mock this per-test.
Element.prototype.scrollIntoView = () => {
  // No-op stub
};

// Establish MSW server lifecycle
beforeAll(() => {
  server.listen();
});

afterEach(() => {
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});

// Export for individual tests that need to override handlers
export { server };
