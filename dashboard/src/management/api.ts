export class ManagementError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "ManagementError";
    this.code = code;
    this.status = status;
  }
}

interface RequestOptions {
  signal?: AbortSignal;
  method?: "GET" | "POST";
  body?: unknown;
}

export async function adminRequest<T>(
  path: string,
  token: string,
  { signal, method = "GET", body }: RequestOptions = {},
): Promise<T> {
  const response = await fetch(`/api/admin/${path}`, {
    method,
    signal,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${token}`,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {
      error?: { code?: string; message?: string };
    } | null;
    throw new ManagementError(
      payload?.error?.message ?? `Management request failed (${response.status})`,
      payload?.error?.code ?? "management_request_failed",
      response.status,
    );
  }
  return await response.json() as T;
}

export function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "The operation could not be completed.";
}

export function abortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}
