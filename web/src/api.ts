// Тонкая обёртка над fetch: куки сессии, JSON, единая обработка ошибок.
export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const init: RequestInit = { method: opts.method || (opts.body || opts.form ? "POST" : "GET"), credentials: "same-origin", headers: {} };
  if (opts.form) init.body = opts.form;
  else if (opts.body !== undefined) { (init.headers as Record<string, string>)["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  if (r.status === 401 && !path.endsWith("/auth/login")) onUnauthorized();
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j); } catch { /* не JSON */ }
    throw new ApiError(r.status, msg);
  }
  const text = await r.text();
  return (text ? JSON.parse(text) : {}) as T;
}
export const get = <T = any>(p: string) => api<T>(p);
export const post = <T = any>(p: string, body?: unknown) => api<T>(p, { method: "POST", body: body ?? {} });
export const patch = <T = any>(p: string, body: unknown) => api<T>(p, { method: "PATCH", body });
export const del = <T = any>(p: string) => api<T>(p, { method: "DELETE" });
export const put = <T = any>(p: string, body: unknown) => api<T>(p, { method: "PUT", body });
