export class LCloudDbError extends Error {
  constructor(
    public status: number,
    public reason: string,
    public detail: unknown,
  ) {
    super(`LCloud DB ${status}: ${reason}`);
  }
}

export function reasonFromDetail(detail: unknown, fallback: string): string {
  return typeof detail === "object" &&
    detail !== null &&
    "detail" in detail &&
    typeof detail.detail === "object" &&
    detail.detail !== null &&
    "reason" in detail.detail
    ? String(detail.detail.reason)
    : fallback;
}
