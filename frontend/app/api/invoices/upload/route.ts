import { backendConfig, publicFailure } from "../../../../lib/backend";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const { url, key } = backendConfig();
    const formData = await request.formData();
    const files = formData.getAll("files");
    if (!files.length || files.length > 10 || files.some(f => !(f instanceof File))) {
      return Response.json({ error: "Select 1–10 PDF invoices." }, { status: 400 });
    }
    const upstream = await fetch(`${url}/api/v1/invoices/upload`, {
      method: "POST", headers: { "x-api-key": key },
      body: formData, cache: "no-store", signal: AbortSignal.timeout(60000),
    });
    const body = await upstream.text();
    return new Response(body, {
      status: upstream.status,
      headers: { "content-type": "application/json", "cache-control": "no-store" },
    });
  } catch (e) { return publicFailure(e); }
}
