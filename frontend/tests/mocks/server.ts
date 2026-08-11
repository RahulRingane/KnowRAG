import { setupServer } from "msw/node";

import { defaultHandlers } from "./handlers";

/**
 * MSW server for all tests. Every test file can override/add handlers
 * via `server.use()` or `server.override()`.
 */
export const server = setupServer(...defaultHandlers);
