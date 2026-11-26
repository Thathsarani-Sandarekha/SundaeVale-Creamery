import { forwardReview, validInvoiceId } from "../../../../../lib/review-proxy";
export const dynamic = "force-dynamic";
export async function PATCH(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!validInvoiceId(id)) return Response.json({ detail: "Invalid invoice identifier" }, { status: 400 });
  return forwardReview(`${id}/review`, "PATCH", request);
}
