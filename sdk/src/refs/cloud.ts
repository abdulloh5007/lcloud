import type { LCloudDbClient } from "../clients/db-client.js";
import type { FileRow, ListInput, Page, UploadMediaOptions } from "../types.js";

export class CloudRef {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly id: number,
  ) {}

  async listFiles(input: ListInput = {}): Promise<Page<FileRow>> {
    return this.client.listFiles(this.id, input);
  }

  async upload(file: Blob, options: UploadMediaOptions = {}): Promise<FileRow> {
    return this.client.uploadFile(this.id, file, options);
  }

  async delete(): Promise<void> {
    await this.client.deleteCloud(this.id);
  }
}

export class FileRef {
  constructor(
    private readonly client: LCloudDbClient,
    private readonly id: number,
  ) {}

  downloadUrl(): string {
    return this.client.fileDownloadUrl(this.id);
  }

  async delete(): Promise<void> {
    await this.client.deleteFile(this.id);
  }
}
