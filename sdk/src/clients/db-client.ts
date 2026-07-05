import { LCloudDbError } from "../errors.js";
import { HttpClient } from "../http.js";
import { CloudRef, FileRef } from "../refs/cloud.js";
import {
  CollectionRef,
  PublicCollectionRef,
} from "../refs/collections.js";
import type {
  AccessRule,
  CloudRow,
  CollectionRow,
  CollectionRules,
  CollectionValidator,
  CreateStoragePublicKeyInput,
  DbPublicKeyRow,
  FileRow,
  JsonObject,
  LCloudDbMeta,
  LCloudDbOptions,
  ListInput,
  Page,
  StoragePublicKeyRow,
  UploadMediaOptions,
  UploadProgress,
  WriteValidator,
} from "../types.js";
import { appendUploadFields, encodePath, listQuery } from "../utils.js";

export class LCloudDbClient {
  private readonly http: HttpClient;

  constructor(options: LCloudDbOptions) {
    this.http = new HttpClient(options);
  }

  collection<T extends JsonObject = JsonObject>(name: string): CollectionRef<T> {
    return new CollectionRef<T>(this, name);
  }

  publicCollection<T extends JsonObject = JsonObject>(
    collectionId: number,
  ): PublicCollectionRef<T> {
    return new PublicCollectionRef<T>(this, collectionId);
  }

  cloud(id: number): CloudRef {
    return new CloudRef(this, id);
  }

  file(id: number): FileRef {
    return new FileRef(this, id);
  }

  async meta(): Promise<LCloudDbMeta> {
    return this.request("/api/v1/db/_meta");
  }

  async createCollection(name: string): Promise<CollectionRow> {
    return this.request("/api/v1/db/collections", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  }

  async listPublicKeys(): Promise<DbPublicKeyRow[]> {
    return this.request("/api/v1/db/public-keys");
  }

  async createPublicKey(label = ""): Promise<DbPublicKeyRow> {
    return this.request("/api/v1/db/public-keys", {
      method: "POST",
      body: JSON.stringify({ label }),
    });
  }

  async revokePublicKey(id: number): Promise<void> {
    await this.request<void>(`/api/v1/db/public-keys/${id}`, {
      method: "DELETE",
    });
  }

  async listStorageKeys(): Promise<StoragePublicKeyRow[]> {
    return this.request("/api/v1/storage/public-keys");
  }

  async createStorageKey(input: CreateStoragePublicKeyInput): Promise<StoragePublicKeyRow> {
    return this.request("/api/v1/storage/public-keys", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async revokeStorageKey(id: number): Promise<void> {
    await this.request<void>(`/api/v1/storage/public-keys/${id}`, {
      method: "DELETE",
    });
  }

  async ensureCollection(name: string): Promise<CollectionRow> {
    try {
      return await this.createCollection(name);
    } catch (error) {
      if (error instanceof LCloudDbError && error.reason === "collection_exists") {
        const found = (await this.listCollections()).find((row) => row.name === name);
        if (found) return found;
      }
      throw error;
    }
  }

  async listCollections(): Promise<CollectionRow[]> {
    return this.request("/api/v1/db/collections");
  }

  async deleteCollection(name: string): Promise<void> {
    await this.request<void>(`/api/v1/db/collections/${encodePath(name)}`, {
      method: "DELETE",
    });
  }

  async getCollectionRules(name: string): Promise<CollectionRules> {
    return this.request(`/api/v1/db/collections/${encodePath(name)}/rules`);
  }

  async setCollectionRules(
    name: string,
    rules: { read: AccessRule; write: AccessRule },
  ): Promise<CollectionRules> {
    return this.request(`/api/v1/db/collections/${encodePath(name)}/rules`, {
      method: "PUT",
      body: JSON.stringify(rules),
    });
  }

  async getCollectionValidator(name: string): Promise<CollectionValidator> {
    return this.request(`/api/v1/db/collections/${encodePath(name)}/validator`);
  }

  async setCollectionValidator(
    name: string,
    validator: WriteValidator,
  ): Promise<CollectionValidator> {
    return this.request(`/api/v1/db/collections/${encodePath(name)}/validator`, {
      method: "PUT",
      body: JSON.stringify(validator),
    });
  }

  async deleteCollectionValidator(name: string): Promise<void> {
    await this.request<void>(`/api/v1/db/collections/${encodePath(name)}/validator`, {
      method: "DELETE",
    });
  }

  async listClouds(): Promise<CloudRow[]> {
    return this.request("/api/v1/clouds");
  }

  async createCloud(name: string): Promise<CloudRow> {
    return this.request("/api/v1/clouds", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  }

  async ensureCloud(name: string): Promise<CloudRow> {
    const found = (await this.listClouds()).find((row) => row.name === name);
    if (found) return found;
    return this.createCloud(name);
  }

  async deleteCloud(id: number): Promise<void> {
    await this.request<void>(`/api/v1/clouds/${id}`, { method: "DELETE" });
  }

  async listFiles(cloudId: number, input: ListInput = {}): Promise<Page<FileRow>> {
    return this.request<Page<FileRow>>(
      `/api/v1/clouds/${cloudId}/files${listQuery(input)}`,
    );
  }

  async uploadFile(
    cloudId: number,
    file: Blob,
    options: UploadMediaOptions = {},
  ): Promise<FileRow> {
    const fd = new FormData();
    appendUploadFields(fd, file, options);
    if (options.lc2) {
      fd.append("client_sha256", options.lc2.clientSha256);
      fd.append("signature", options.lc2.signature);
      fd.append("ts", String(options.lc2.ts));
    }

    const path = `/api/v1/clouds/${cloudId}/files`;
    if (options.onProgress && typeof XMLHttpRequest !== "undefined") {
      return this.uploadWithXhr(path, fd, options.onProgress);
    }
    return this.request<FileRow>(path, {
      method: "POST",
      body: fd,
    });
  }

  async deleteFile(id: number): Promise<void> {
    await this.request<void>(`/api/v1/files/${id}`, { method: "DELETE" });
  }

  fileDownloadUrl(id: number): string {
    return this.url(`/api/v1/files/${id}/download`);
  }

  request<T>(path: string, init: RequestInit = {}): Promise<T> {
    return this.http.request<T>(path, init);
  }

  url(path: string): string {
    return this.http.url(path);
  }

  uploadWithXhr(
    path: string,
    body: FormData,
    onProgress: (progress: UploadProgress) => void,
  ): Promise<FileRow> {
    return this.http.uploadWithXhr(path, body, onProgress);
  }
}
