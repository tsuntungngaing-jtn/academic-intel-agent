/**
 * FastAPI backend on the HPC login/service host (port 9105 by policy).
 * Set NEXT_PUBLIC_API_BASE_URL at build time, e.g. http://10.x.x.x:9105
 */
export function getApiBaseUrl(): string {
  const raw = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (raw) {
    return raw.replace(/\/+$/, "");
  }
  return "http://127.0.0.1:9105";
}
