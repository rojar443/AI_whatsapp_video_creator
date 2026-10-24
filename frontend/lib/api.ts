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
  phone: string | null;
  role: "user" | "admin";
  is_anonymous: boolean;
  email_verified: boolean;
  credits: number;
  telegram: { enabled: boolean; linked: boolean; name?: string | null; linked_at: string | null };
  whatsapp: { enabled: boolean; status: "linked" | "pending" | "not_linked"; number: string | null; linked_at: string | null; business_number: string };
};

export type Asset = {
  id: string; kind: string; url: string | null; width: number | null; height: number | null;
  duration_seconds: number | null; is_demo: boolean; demo: string[] | null; title: string | null;
  created_at: string; expires_at: string | null; size_bytes: number; job_id: string | null; deleted: boolean;
};

export type Job = {
  id: string; revision: number; status: string; stage: string | null; attempts: number; run_number: number;
  credits_charged: number; pipeline: Record<string, string> | null; error_code: string | null;
  error_message: string | null; created_at: string; finished_at: string | null;
};

export type ProjectSummary = {
  id: string; title: string; status: string; stage: string | null; revision: number; last_error: string | null;
  sources: number; created_at: string; updated_at: string; video: Asset | null;
};

export type PlanItem = {
  segment_id: string; asset_id: string; kind: string; source_start: number; source_end: number;
  role: "hook" | "build" | "payoff"; reason: string; description: string;
};

export type Plan = {
  items: PlanItem[]; total_seconds: number; dropped: { asset_id: string; label: string; reason: string }[];
  notes: string[]; script: { title: string; lines: { item_index: number; text: string }[] } | null;
  music_title: string | null; scorer: string; pipeline: Record<string, string>; labels: Record<string, string>;
};

export type ProjectDetail = ProjectSummary & {
  preferences: { revision: number; values: Record<string, any>; sources: Record<string, string>;
                 playbook: string | null; plan: Plan | null }[];
  jobs: Job[];
  assets: Asset[];
  source_files: { id: string; label: string; kind: string; status: string; reject_reason: string | null;
                  duration_seconds: number | null }[];
  can_retry: boolean;
};

export const STATUS_LABEL: Record<string, string> = {
  collecting_media: "Receiving clips",
  awaiting_preferences: "Planning",
  awaiting_confirmation: "Waiting for your OK",
  queued: "Queued",
  processing: "Editing",
  completed: "Ready",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function fmtDate(s: string | null | undefined): string {
  if (!s) return "—";
  return new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function mmss(t: number): string {
  return `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
}
