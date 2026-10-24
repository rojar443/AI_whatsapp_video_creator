// Tiny API client. All calls are same-origin (/api/* is proxied to the backend by Next).

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function csrfToken(): string {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/(?:^|;\s*)rc_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export async function api<T = any>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("content-type", "application/json");
    body = JSON.stringify(init.json);
  }
  if (method !== "GET") headers.set("x-csrf-token", csrfToken());
  const res = await fetch(path, { ...init, method, headers, body, credentials: "same-origin" });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    let msg = data?.detail ?? `Request failed (${res.status})`;
    if (Array.isArray(msg)) msg = msg.map((d: any) => d.msg?.replace(/^Value error, /, "")).join(". ");
    throw new ApiError(res.status, String(msg));
  }
  return data as T;
}

export type Me = {
  id: string;
  name: string;
  email: string;
  phone: string;
  role: "user" | "admin";
  email_verified: boolean;
  credits: number;
  whatsapp: { status: "linked" | "pending" | "not_linked"; number: string | null; linked_at: string | null; business_number: string };
};

export type Asset = {
  id: string; kind: string; url: string | null; width: number | null; height: number | null;
  duration_seconds: number | null; is_mock: boolean; title: string | null; created_at: string;
  expires_at: string | null; size_bytes: number; job_id: string | null; deleted: boolean;
};

export type Job = {
  id: string; revision: number; status: string; stage: string | null; attempts: number; run_number: number;
  credits_charged: number; provider: string | null; is_mock: boolean; error_code: string | null;
  error_message: string | null; created_at: string; finished_at: string | null;
};

export type ProjectSummary = {
  id: string; title: string; status: string; revision: number; last_error: string | null;
  created_at: string; updated_at: string; video: Asset | null;
};

export type ProjectDetail = ProjectSummary & {
  preferences: { revision: number; values: Record<string, any>; sources: Record<string, string>; credits_cost: number }[];
  jobs: Job[];
  assets: Asset[];
  can_retry: boolean;
};

export const STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  awaiting_input: "Collecting details",
  awaiting_confirmation: "Waiting for your OK",
  queued: "Queued",
  processing: "Generating",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
