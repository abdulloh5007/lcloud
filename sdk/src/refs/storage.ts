import type { LCloudDbClient } from "../clients/db-client.js";
import type { FileRow, ListInput, Page, UploadMediaOptions } from "../types.js";
import { appendUploadFields, encodePath, listQuery } from "../utils.js";

export class PublicStorageRef {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly storageKey: string,
  ) {}

  private get path(): string {
    return `/api/v1/public/storage/key/${encodePath(this.storageKey)}/files`;
  }

  async listFiles(input: ListInput = {}): Promise<Page<FileRow>> {
    return this.client.request<Page<FileRow>>(`${this.path}${listQuery(input)}`);
  }

  async upload(file: Blob, options: UploadMediaOptions = {}): Promise<FileRow> {
    const fd = new FormData();
    appendUploadFields(fd, file, options);
    if (options.onProgress && typeof XMLHttpRequest !== "undefined") {
      return this.client.uploadWithXhr(this.path, fd, options.onProgress);
    }
    return this.client.request<FileRow>(this.path, {
      method: "POST",
      body: fd,
    });
  }

  file(id: number): PublicStorageFileRef {
    return new PublicStorageFileRef(this.client, this.storageKey, id);
  }

  downloadUrl(id: number): string {
    return this.file(id).downloadUrl();
  }

  async deleteFile(id: number): Promise<void> {
    await this.file(id).delete();
  }
}

export class PublicStorageFileRef {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly storageKey: string,
    private readonly id: number,
  ) {}

  private get path(): string {
    return `/api/v1/public/storage/key/${encodePath(this.storageKey)}/files/${this.id}`;
  }

  downloadUrl(): string {
    return this.client.url(`${this.path}/download`);
  }

  async delete(): Promise<void> {
    await this.client.request<void>(this.path, { method: "DELETE" });
  }
}
