import { backendConfig, publicFailure } from "../../../lib/backend";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const { url, key } = backendConfig();
    const upstream = await fetch(`${url}/api/v1/invoices`, {
      headers: { "x-api-key": key }, cache: "no-store", signal: AbortSignal.timeout(10000),
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  } catch (e) { return publicFailure(e); }
}
