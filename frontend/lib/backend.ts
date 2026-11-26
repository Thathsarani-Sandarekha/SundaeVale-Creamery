import "server-only";
import { config } from "dotenv";
import { resolve } from "path";

// Development only: read the same root .env used by FastAPI and the worker.
// Never return this key in the browser, include it in NEXT_PUBLIC_*, or log it.
const root = resolve(process.cwd(), "..");
config({ path: resolve(root, ".env"), override: false });
config({ path: resolve(root, ".env.invoice"), override: false });

export function backendConfig() {
  const key = process.env.INVOICE_API_KEY ?? "";
  if (!key || key.startsWith("YOUR_") || key.startsWith("REPLACE_WITH")) {
    throw new Error("INVOICE_API_KEY is missing or still a placeholder in the root .env");
  }
  return {
    url: (process.env.INVOICE_API_BASE_URL || "http://127.0.0.1:8001").replace(/\/$/, ""),
    key,
  };
}

export function publicFailure(error: unknown): Response {
  const isConfigurationError = error instanceof Error && error.message.includes("INVOICE_API_KEY");
  return Response.json({ error: isConfigurationError
    ? "Configure INVOICE_API_KEY in the root .env (see README)."
    : "Could not reach the invoice API. Verify FastAPI is running on port 8001." },
    { status: 503 });
}
