import { NextResponse } from "next/server";

/**
 * Browser calls same-origin POST /api/start_analyze; this route forwards to FastAPI.
 * Prefer ACADEMIC_API_URL on the server if it differs from the public URL.
 */
function backendBase(): string {
  const raw =
    process.env.ACADEMIC_API_URL?.trim() ||
    process.env.NEXT_PUBLIC_API_BASE_URL?.trim() ||
    "http://127.0.0.1:9105";
  return raw.replace(/\/+$/, "");
}

export async function POST(request: Request) {
  const body = await request.text();
  const url = `${backendBase()}/start_analyze`;
  try {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body.length > 0 ? body : "{}",
    });
    const text = await r.text();
    let data: unknown = {};
    if (text) {
      try {
        data = JSON.parse(text) as unknown;
      } catch {
        data = { raw: text };
      }
    }
    return NextResponse.json(data, { status: r.status });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { detail: `proxy failed: ${message}`, upstream: url },
      { status: 502 },
    );
  }
}
