import "server-only";
import { backendConfig, publicFailure } from "./backend";

export function validInvoiceId(id: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
}

export async function forwardReview(path: string, method: "GET" | "PATCH" | "POST", request?: Request): Promise<Response> {
  try {
    const { url, key } = backendConfig();
    let body: string | undefined;
    if (request && method !== "GET") {
      body = await request.text();
      if (body.length > 120000) {
        return Response.json({ detail: "Review payload too large" }, { status: 413 });
      }
    }
    const upstream = await fetch(`${url}/api/v1/invoices/${path}`, {
      method,
      headers: { "x-api-key": key, ...(body === undefined ? {} : { "content-type": "application/json" }) },
      body,
      signal: AbortSignal.timeout(30000),
      cache: "no-store",
    });
    const contentType = upstream.headers.get("content-type") ?? "application/json";
    const headers = new Headers({ "content-type": contentType, "cache-control": "no-store", "x-content-type-options": "nosniff" });
    if (upstream.ok && (contentType.startsWith("application/pdf") || contentType.startsWith("text/csv"))) {
      const contentDisposition = upstream.headers.get("content-disposition");
      if (contentDisposition) headers.set("content-disposition", contentDisposition);
      return new Response(await upstream.arrayBuffer(), { status: upstream.status, headers });
    }
    return new Response(await upstream.text(), { status: upstream.status, headers });
  } catch (error) {
    return publicFailure(error);
  }
}
