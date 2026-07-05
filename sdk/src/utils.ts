export function encodePath(value: string): string {
  return encodeURIComponent(value);
}

export function isFormData(body: unknown): boolean {
  return typeof FormData !== "undefined" && body instanceof FormData;
}

export function listQuery(input: { limit?: number; offset?: number } = {}): string {
  const qs = new URLSearchParams();
  if (input.limit !== undefined) qs.set("limit", String(input.limit));
  if (input.offset !== undefined) qs.set("offset", String(input.offset));
  const query = qs.toString();
  return query ? `?${query}` : "";
}

export function appendUploadFields(
  formData: FormData,
  file: Blob,
  options: { name?: string; compress?: boolean } = {},
): void {
  const name =
    options.name ??
    ("name" in file && typeof file.name === "string" ? file.name : "upload.bin");
  formData.append("file", file, name);
  formData.append("compress", String(options.compress ?? true));
}
