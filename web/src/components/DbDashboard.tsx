import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Braces,
  Check,
  Copy,
  Database,
  FileJson,
  Globe2,
  KeyRound,
  Plus,
  UploadCloud,
  RefreshCw,
  Save,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { ApiError, clouds, jsonDb } from "@/api/client";
import type {
  JsonAccessRule,
  JsonCollectionRow,
  JsonDbEvent,
  JsonDbPublicKeyRow,
  JsonDocumentRow,
  JsonStoragePublicKeyRow,
  JsonQueryInput,
  JsonWhereOp,
  JsonWriteValidator,
} from "@/api/types";
import { classNames, formatDate } from "@/lib/format";
import { Button } from "./ui/Button";

const PAGE_LIMIT = 50;
const EMPTY_DOC = "{\n  \n}";
const EMPTY_VALIDATOR = "{\n  \"max_bytes\": 102400,\n  \"max_fields\": 20,\n  \"required_fields\": [],\n  \"allowed_fields\": []\n}";
const RULES: Array<{ value: JsonAccessRule; label: string }> = [
  { value: "owner", label: "Project owner" },
  { value: "document_owner", label: "Document owner" },
  { value: "authenticated", label: "Signed in" },
  { value: "public", label: "Public" },
];
const OPS: JsonWhereOp[] = ["==", "!=", "<", "<=", ">", ">=", "contains", "startsWith"];

type WriteMode = "create" | "set" | "patch";
type DbPage = "documents" | "editor" | "rules" | "keys" | "events";

const DB_PAGES: Array<{ id: DbPage; label: string; icon: LucideIcon }> = [
  { id: "documents", label: "Documents", icon: FileJson },
  { id: "editor", label: "Editor", icon: Braces },
  { id: "rules", label: "Rules", icon: Globe2 },
  { id: "keys", label: "Keys", icon: KeyRound },
  { id: "events", label: "Events", icon: Activity },
];

interface QueryDraft {
  field: string;
  op: JsonWhereOp;
  value: string;
  orderBy: string;
  order: "asc" | "desc";
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function parseJsonObject(raw: string): Record<string, unknown> {
  const parsed = JSON.parse(raw) as unknown;
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("JSON должен быть object, не array/string/null.");
  }
  return parsed as Record<string, unknown>;
}

function parseQueryValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return raw;
  }
}

function errorText(e: unknown): string {
  if (e instanceof ApiError) return `${e.reason} (${e.status})`;
  if (e instanceof Error) return e.message;
  return "unknown error";
}

export function DbDashboard() {
  const qc = useQueryClient();
  const [selectedName, setSelectedName] = useState<string>("");
  const [newCollection, setNewCollection] = useState("");
  const [createCollectionError, setCreateCollectionError] = useState<string | null>(null);
  const [queryDraft, setQueryDraft] = useState<QueryDraft>({
    field: "",
    op: "==",
    value: "",
    orderBy: "",
    order: "asc",
  });
  const [queryInput, setQueryInput] = useState<JsonQueryInput | null>(null);
  const [selectedDoc, setSelectedDoc] = useState<JsonDocumentRow | null>(null);
  const [docId, setDocId] = useState("");
  const [docJson, setDocJson] = useState(EMPTY_DOC);
  const [writeMode, setWriteMode] = useState<WriteMode>("create");
  const [docError, setDocError] = useState<string | null>(null);
  const [readRule, setReadRule] = useState<JsonAccessRule>("owner");
  const [writeRule, setWriteRule] = useState<JsonAccessRule>("owner");
  const [validatorText, setValidatorText] = useState(EMPTY_VALIDATOR);
  const [rulesError, setRulesError] = useState<string | null>(null);
  const [validatorError, setValidatorError] = useState<string | null>(null);
  const [events, setEvents] = useState<JsonDbEvent[]>([]);
  const [lastEventId, setLastEventId] = useState(0);
  const [copiedPath, setCopiedPath] = useState(false);
  const [activePage, setActivePage] = useState<DbPage>("documents");
  const [newPublicKeyLabel, setNewPublicKeyLabel] = useState("");
  const [newStorageKeyLabel, setNewStorageKeyLabel] = useState("");
  const [selectedStorageCloudId, setSelectedStorageCloudId] = useState<string>("");
  const [storageMaxBytes, setStorageMaxBytes] = useState("10485760");
  const [storageAllowUpload, setStorageAllowUpload] = useState(true);
  const [storageAllowList, setStorageAllowList] = useState(true);
  const [storageAllowDownload, setStorageAllowDownload] = useState(true);
  const [storageAllowDelete, setStorageAllowDelete] = useState(false);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const collectionsQ = useQuery({
    queryKey: ["json-db", "collections"],
    queryFn: () => jsonDb.listCollections(),
  });

  const metaQ = useQuery({
    queryKey: ["json-db", "meta"],
    queryFn: () => jsonDb.meta(),
    staleTime: 5 * 60_000,
  });

  const selectedCollection = useMemo(
    () => (collectionsQ.data ?? []).find((c) => c.name === selectedName) ?? null,
    [collectionsQ.data, selectedName],
  );

  useEffect(() => {
    const rows = collectionsQ.data ?? [];
    if (rows.length === 0) {
      setSelectedName("");
      return;
    }
    if (!selectedName || !rows.some((c) => c.name === selectedName)) {
      setSelectedName(rows[0].name);
    }
  }, [collectionsQ.data, selectedName]);

  const documentsQ = useQuery({
    queryKey: ["json-db", "documents", selectedName, queryInput],
    enabled: Boolean(selectedName),
    queryFn: () => {
      if (!selectedName) {
        return Promise.resolve({ items: [], total: 0, limit: PAGE_LIMIT, offset: 0 });
      }
      if (queryInput) {
        return jsonDb.queryDocuments(selectedName, {
          ...queryInput,
          limit: PAGE_LIMIT,
          offset: 0,
        });
      }
      return jsonDb.listDocuments(selectedName, { limit: PAGE_LIMIT, offset: 0 });
    },
  });

  const rulesQ = useQuery({
    queryKey: ["json-db", "rules", selectedName],
    enabled: Boolean(selectedName),
    queryFn: () => jsonDb.getRules(selectedName),
  });

  const validatorQ = useQuery({
    queryKey: ["json-db", "validator", selectedName],
    enabled: Boolean(selectedName),
    queryFn: () => jsonDb.getValidator(selectedName),
  });

  const publicKeysQ = useQuery({
    queryKey: ["json-db", "public-keys"],
    queryFn: () => jsonDb.listPublicKeys(),
  });

  const storageKeysQ = useQuery({
    queryKey: ["json-db", "storage-public-keys"],
    queryFn: () => jsonDb.listStorageKeys(),
  });

  const cloudsQ = useQuery({
    queryKey: ["clouds"],
    queryFn: () => clouds.list(),
  });

  useEffect(() => {
    if (!rulesQ.data) return;
    setReadRule(rulesQ.data.read);
    setWriteRule(rulesQ.data.write);
    setRulesError(null);
  }, [rulesQ.data]);

  useEffect(() => {
    if (!validatorQ.data) return;
    setValidatorText(
      validatorQ.data.validator ? prettyJson(validatorQ.data.validator) : EMPTY_VALIDATOR,
    );
    setValidatorError(null);
  }, [validatorQ.data]);

  useEffect(() => {
    setSelectedDoc(null);
    setDocId("");
    setDocJson(EMPTY_DOC);
    setWriteMode("create");
    setDocError(null);
    setEvents([]);
    setLastEventId(0);
  }, [selectedName]);

  useEffect(() => {
    if (!selectedName) return;
    const source = new EventSource(jsonDb.eventsUrl(selectedName, 0));
    const onChange = (event: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(event.data) as JsonDbEvent;
        setLastEventId(parsed.id);
        setEvents((current) => [parsed, ...current].slice(0, 40));
        void qc.invalidateQueries({ queryKey: ["json-db", "documents", selectedName] });
        void qc.invalidateQueries({ queryKey: ["json-db", "collections"] });
      } catch {
        // Ignore malformed SSE frames; the stream will keep running.
      }
    };
    source.addEventListener("lcloud.db.change", onChange as EventListener);
    return () => source.close();
  }, [qc, selectedName]);

  const createCollection = useMutation({
    mutationFn: (name: string) => jsonDb.createCollection(name),
    onSuccess: (row) => {
      setNewCollection("");
      setCreateCollectionError(null);
      setSelectedName(row.name);
      setActivePage("documents");
      void qc.invalidateQueries({ queryKey: ["json-db", "collections"] });
    },
    onError: (e) => setCreateCollectionError(errorText(e)),
  });

  const deleteCollection = useMutation({
    mutationFn: (name: string) => jsonDb.deleteCollection(name),
    onSuccess: () => {
      setSelectedName("");
      void qc.invalidateQueries({ queryKey: ["json-db"] });
    },
  });

  const writeDocument = useMutation({
    mutationFn: async () => {
      if (!selectedName) throw new Error("Выберите collection.");
      const data = parseJsonObject(docJson);
      const cleanId = docId.trim();
      if (writeMode === "create") {
        return jsonDb.createDocument(selectedName, {
          id: cleanId || undefined,
          data,
        });
      }
      if (!cleanId) throw new Error("Для set/patch нужен document id.");
      if (writeMode === "set") return jsonDb.setDocument(selectedName, cleanId, data);
      return jsonDb.patchDocument(selectedName, cleanId, data);
    },
    onSuccess: (row) => {
      setDocError(null);
      setSelectedDoc(row);
      setDocId(row.id);
      setDocJson(prettyJson(row.data));
      setWriteMode("patch");
      void qc.invalidateQueries({ queryKey: ["json-db", "documents", selectedName] });
      void qc.invalidateQueries({ queryKey: ["json-db", "collections"] });
    },
    onError: (e) => setDocError(errorText(e)),
  });

  const deleteDocument = useMutation({
    mutationFn: (row: JsonDocumentRow) => jsonDb.deleteDocument(selectedName, row.id),
    onSuccess: () => {
      setSelectedDoc(null);
      setDocId("");
      setDocJson(EMPTY_DOC);
      setWriteMode("create");
      void qc.invalidateQueries({ queryKey: ["json-db", "documents", selectedName] });
      void qc.invalidateQueries({ queryKey: ["json-db", "collections"] });
    },
  });

  const saveRules = useMutation({
    mutationFn: () => jsonDb.setRules(selectedName, { read: readRule, write: writeRule }),
    onSuccess: (row) => {
      setRulesError(null);
      setReadRule(row.read);
      setWriteRule(row.write);
      void qc.invalidateQueries({ queryKey: ["json-db", "rules", selectedName] });
      void qc.invalidateQueries({ queryKey: ["json-db", "collections"] });
    },
    onError: (e) => setRulesError(errorText(e)),
  });

  const saveValidator = useMutation({
    mutationFn: () => {
      const parsed = parseJsonObject(validatorText) as JsonWriteValidator;
      return jsonDb.setValidator(selectedName, parsed);
    },
    onSuccess: (row) => {
      setValidatorError(null);
      setValidatorText(row.validator ? prettyJson(row.validator) : EMPTY_VALIDATOR);
      void qc.invalidateQueries({ queryKey: ["json-db", "validator", selectedName] });
      void qc.invalidateQueries({ queryKey: ["json-db", "collections"] });
    },
    onError: (e) => setValidatorError(errorText(e)),
  });

  const clearValidator = useMutation({
    mutationFn: () => jsonDb.deleteValidator(selectedName),
    onSuccess: () => {
      setValidatorText(EMPTY_VALIDATOR);
      setValidatorError(null);
      void qc.invalidateQueries({ queryKey: ["json-db", "validator", selectedName] });
      void qc.invalidateQueries({ queryKey: ["json-db", "collections"] });
    },
  });

  const createPublicKey = useMutation({
    mutationFn: (label: string) => jsonDb.createPublicKey(label),
    onSuccess: (row) => {
      setNewPublicKeyLabel("");
      setCopiedKey(row.key);
      void qc.invalidateQueries({ queryKey: ["json-db", "public-keys"] });
    },
  });

  const revokePublicKey = useMutation({
    mutationFn: (id: number) => jsonDb.revokePublicKey(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["json-db", "public-keys"] });
    },
  });

  const createStorageKey = useMutation({
    mutationFn: () => {
      const cloudId = Number(selectedStorageCloudId);
      if (!cloudId) throw new Error("Select a cloud for media storage.");
      const maxBytes = storageMaxBytes.trim() ? Number(storageMaxBytes) : null;
      if (maxBytes !== null && (!Number.isFinite(maxBytes) || maxBytes <= 0)) {
        throw new Error("Max file bytes must be a positive number.");
      }
      return jsonDb.createStorageKey({
        cloud_id: cloudId,
        label: newStorageKeyLabel.trim(),
        allow_upload: storageAllowUpload,
        allow_list: storageAllowList,
        allow_download: storageAllowDownload,
        allow_delete: storageAllowDelete,
        max_file_bytes: maxBytes,
      });
    },
    onSuccess: (row) => {
      setNewStorageKeyLabel("");
      setCopiedKey(row.key);
      void qc.invalidateQueries({ queryKey: ["json-db", "storage-public-keys"] });
    },
  });

  const revokeStorageKey = useMutation({
    mutationFn: (id: number) => jsonDb.revokeStorageKey(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["json-db", "storage-public-keys"] });
    },
  });

  function applyQuery() {
    const where =
      queryDraft.field.trim() === ""
        ? []
        : [
            {
              field: queryDraft.field.trim(),
              op: queryDraft.op,
              value: parseQueryValue(queryDraft.value),
            },
          ];
    setQueryInput({
      where,
      order_by: queryDraft.orderBy.trim() || null,
      order: queryDraft.order,
    });
  }

  function resetQuery() {
    setQueryDraft({ field: "", op: "==", value: "", orderBy: "", order: "asc" });
    setQueryInput(null);
  }

  function openDocument(row: JsonDocumentRow) {
    setSelectedDoc(row);
    setDocId(row.id);
    setDocJson(prettyJson(row.data));
    setWriteMode("patch");
    setDocError(null);
    setActivePage("editor");
  }

  function newDocument() {
    setSelectedDoc(null);
    setDocId("");
    setDocJson(EMPTY_DOC);
    setWriteMode("create");
    setDocError(null);
    setActivePage("editor");
  }

  const publicPath = rulesQ.data?.public_base_path ?? "";
  const hasCollections = (collectionsQ.data ?? []).length > 0;
  const currentPage = DB_PAGES.find((page) => page.id === activePage) ?? DB_PAGES[0];

  return (
    <main className="flex-1 min-w-0 bg-bg dark:bg-bg-dark text-neutral-900 dark:text-neutral-100">
      <div className="flex h-full min-h-0 flex-col">
        <header className="border-b border-neutral-200 dark:border-neutral-800 bg-panel dark:bg-panel-dark px-3 py-3 sm:px-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Database size={18} className="text-blue-600 dark:text-blue-400" />
                <h1 className="text-base font-semibold">LCloud DB console</h1>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-neutral-500">
                <span className="tabular-nums">
                  {collectionsQ.data?.length ?? 0} collections
                </span>
                {metaQ.data && (
                  <>
                    <span aria-hidden="true">·</span>
                    <span className="tabular-nums">
                      max {metaQ.data.pagination.max_limit} docs/page
                    </span>
                    <span aria-hidden="true">·</span>
                    <span>SSE {metaQ.data.realtime.poll_seconds}s</span>
                  </>
                )}
                {selectedCollection && (
                  <>
                    <span aria-hidden="true">·</span>
                    <span className="truncate">
                      active: <span className="font-mono">{selectedCollection.name}</span>
                    </span>
                  </>
                )}
                <span aria-hidden="true">·</span>
                <span>
                  page: <span className="font-medium text-neutral-700 dark:text-neutral-300">{currentPage.label}</span>
                </span>
              </div>
            </div>
            <div className="flex min-w-0 items-center gap-2">
              <label className="relative min-w-0">
                <span className="sr-only">DB console page</span>
                <select
                  value={activePage}
                  onChange={(e) => setActivePage(e.target.value as DbPage)}
                  className="h-10 w-40 rounded-lg border border-neutral-200 bg-bg px-3 text-sm font-medium dark:border-neutral-700 dark:bg-bg-dark sm:w-44"
                >
                  {DB_PAGES.map((page) => (
                    <option key={page.id} value={page.id}>
                      {page.label}
                    </option>
                  ))}
                </select>
              </label>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  void collectionsQ.refetch();
                  void documentsQ.refetch();
                  void rulesQ.refetch();
                  void validatorQ.refetch();
                }}
              >
                <RefreshCw size={14} />
                <span className="hidden sm:inline">Refresh</span>
              </Button>
            </div>
          </div>
          <nav className="mt-3 hidden items-center gap-1 overflow-x-auto md:flex">
            {DB_PAGES.map((page) => {
              const Icon = page.icon;
              return (
                <button
                  key={page.id}
                  type="button"
                  onClick={() => setActivePage(page.id)}
                  className={classNames(
                    "inline-flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm font-medium transition-[background-color,color,scale] duration-150 ease-out active:scale-[0.96]",
                    activePage === page.id
                      ? "bg-blue-600 text-white"
                      : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800",
                  )}
                >
                  <Icon size={15} />
                  {page.label}
                </button>
              );
            })}
          </nav>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)]">
          <aside className="flex min-h-0 flex-col border-b border-neutral-200 bg-panel dark:border-neutral-800 dark:bg-panel-dark lg:border-b-0 lg:border-r">
            <div className="flex items-center justify-between gap-2 px-3 py-3">
              <span className="text-xs font-medium uppercase tracking-wide text-neutral-500">
                Collections
              </span>
              <span className="text-xs tabular-nums text-neutral-400">
                {collectionsQ.data?.length ?? 0}
              </span>
            </div>
            <form
              className="flex gap-2 px-3 pb-3"
              onSubmit={(e) => {
                e.preventDefault();
                const name = newCollection.trim();
                if (name) createCollection.mutate(name);
              }}
            >
              <input
                value={newCollection}
                onChange={(e) => setNewCollection(e.target.value)}
                placeholder="new_collection"
                className="min-w-0 flex-1 rounded-lg border border-neutral-200 bg-bg px-3 py-2 text-sm dark:border-neutral-700 dark:bg-bg-dark"
              />
              <Button
                type="submit"
                size="sm"
                loading={createCollection.isPending}
                disabled={!newCollection.trim()}
                aria-label="Create collection"
              >
                <Plus size={14} />
              </Button>
            </form>
            {createCollectionError && (
              <div className="mx-3 mb-3 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
                {createCollectionError}
              </div>
            )}
            <div className="max-h-56 overflow-y-auto px-2 pb-2 thin-scroll lg:max-h-none lg:flex-1">
              {collectionsQ.isLoading && (
                <div className="px-3 py-2 text-sm text-neutral-500">…</div>
              )}
              {collectionsQ.isError && (
                <div className="px-3 py-2 text-sm text-red-600">
                  {errorText(collectionsQ.error)}
                </div>
              )}
              {(collectionsQ.data ?? []).map((collection) => (
                <CollectionButton
                  key={collection.id}
                  collection={collection}
                  active={collection.name === selectedName}
                  onSelect={() => setSelectedName(collection.name)}
                />
              ))}
              {!collectionsQ.isLoading && !hasCollections && (
                <div className="px-3 py-8 text-center text-sm text-neutral-400">
                  Создайте collection, чтобы начать писать документы.
                </div>
              )}
            </div>
            {selectedCollection && (
              <div className="border-t border-neutral-200 p-2 dark:border-neutral-800">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="w-full justify-start text-red-600 hover:bg-red-50 hover:text-red-700 dark:text-red-400 dark:hover:bg-red-950/30"
                  loading={deleteCollection.isPending}
                  onClick={() => {
                    if (window.confirm(`Delete collection ${selectedCollection.name}?`)) {
                      deleteCollection.mutate(selectedCollection.name);
                    }
                  }}
                >
                  <Trash2 size={14} />
                  Delete collection
                </Button>
              </div>
            )}
          </aside>

          <section
            className={classNames(
              "min-h-0 border-b border-neutral-200 dark:border-neutral-800 lg:border-b-0",
              activePage !== "documents" && "hidden",
            )}
          >
            <div className="flex h-full min-h-0 flex-col">
              <div className="border-b border-neutral-200 bg-panel/70 px-3 py-3 dark:border-neutral-800 dark:bg-panel-dark/70">
                <div className="flex flex-wrap items-center gap-2">
                  <div className="relative min-w-[12rem] flex-1">
                    <Search
                      size={14}
                      className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-neutral-400"
                    />
                    <input
                      value={queryDraft.field}
                      onChange={(e) =>
                        setQueryDraft((current) => ({
                          ...current,
                          field: e.target.value,
                        }))
                      }
                      placeholder="field path: profile.city"
                      className="w-full rounded-lg border border-neutral-200 bg-bg py-2 pl-8 pr-3 text-sm dark:border-neutral-700 dark:bg-bg-dark"
                      disabled={!selectedName}
                    />
                  </div>
                  <select
                    value={queryDraft.op}
                    onChange={(e) =>
                      setQueryDraft((current) => ({
                        ...current,
                        op: e.target.value as JsonWhereOp,
                      }))
                    }
                    className="h-10 rounded-lg border border-neutral-200 bg-bg px-2 text-sm dark:border-neutral-700 dark:bg-bg-dark"
                    disabled={!selectedName}
                  >
                    {OPS.map((op) => (
                      <option key={op} value={op}>
                        {op}
                      </option>
                    ))}
                  </select>
                  <input
                    value={queryDraft.value}
                    onChange={(e) =>
                      setQueryDraft((current) => ({
                        ...current,
                        value: e.target.value,
                      }))
                    }
                    placeholder="value"
                    className="min-w-[9rem] flex-1 rounded-lg border border-neutral-200 bg-bg px-3 py-2 text-sm dark:border-neutral-700 dark:bg-bg-dark sm:flex-none"
                    disabled={!selectedName}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={resetQuery}
                    disabled={!selectedName}
                  >
                    <X size={14} />
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    onClick={applyQuery}
                    disabled={!selectedName}
                  >
                    <Search size={14} />
                    Query
                  </Button>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-neutral-500">
                  <span className="tabular-nums">
                    {documentsQ.data?.total ?? 0} docs
                  </span>
                  <input
                    value={queryDraft.orderBy}
                    onChange={(e) =>
                      setQueryDraft((current) => ({
                        ...current,
                        orderBy: e.target.value,
                      }))
                    }
                    placeholder="order_by"
                    className="h-8 rounded-md border border-neutral-200 bg-bg px-2 text-xs dark:border-neutral-700 dark:bg-bg-dark"
                    disabled={!selectedName}
                  />
                  <select
                    value={queryDraft.order}
                    onChange={(e) =>
                      setQueryDraft((current) => ({
                        ...current,
                        order: e.target.value as "asc" | "desc",
                      }))
                    }
                    className="h-8 rounded-md border border-neutral-200 bg-bg px-2 text-xs dark:border-neutral-700 dark:bg-bg-dark"
                    disabled={!selectedName}
                  >
                    <option value="asc">asc</option>
                    <option value="desc">desc</option>
                  </select>
                  {queryInput && (
                    <span className="rounded-md bg-blue-50 px-2 py-1 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">
                      filtered
                    </span>
                  )}
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto p-3 thin-scroll">
                {!selectedName && (
                  <EmptyState
                    icon={Database}
                    title="Нет выбранной collection"
                    text="Создайте или выберите collection слева."
                  />
                )}
                {selectedName && documentsQ.isLoading && (
                  <div className="text-sm text-neutral-500">…</div>
                )}
                {selectedName && documentsQ.isError && (
                  <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300">
                    {errorText(documentsQ.error)}
                  </div>
                )}
                {selectedName && documentsQ.data?.items.length === 0 && !documentsQ.isLoading && (
                  <EmptyState
                    icon={FileJson}
                    title="Документов нет"
                    text="Создайте первый JSON document на странице Editor."
                  />
                )}
                <div className="grid gap-2">
                  {(documentsQ.data?.items ?? []).map((row) => (
                    <DocumentRow
                      key={row.id}
                      row={row}
                      active={selectedDoc?.id === row.id}
                      onOpen={() => openDocument(row)}
                    />
                  ))}
                </div>
              </div>
            </div>
          </section>

          <section
            className={classNames(
              "min-h-0 overflow-y-auto bg-bg p-3 dark:bg-bg-dark thin-scroll",
              activePage === "documents" && "hidden",
            )}
          >
            <div className="mx-auto w-full max-w-5xl space-y-4">
              <section
                className={classNames(
                  "rounded-lg bg-panel p-3 surface-shadow dark:bg-panel-dark",
                  activePage !== "editor" && "hidden",
                )}
              >
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div>
                    <h2 className="text-sm font-semibold">Document editor</h2>
                    <p className="text-xs text-neutral-500">
                      create / set / shallow patch
                    </p>
                  </div>
                  <Button type="button" variant="ghost" size="sm" onClick={newDocument}>
                    <Plus size={14} />
                  </Button>
                </div>
                <div className="grid grid-cols-3 gap-2">
                  {(["create", "set", "patch"] as WriteMode[]).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => setWriteMode(mode)}
                      className={classNames(
                        "min-h-10 rounded-lg px-2 text-xs font-medium transition-[background-color,color,scale] duration-150 ease-out active:scale-[0.96]",
                        writeMode === mode
                          ? "bg-blue-600 text-white"
                          : "bg-panel text-neutral-600 hover:bg-neutral-100 dark:bg-panel-dark dark:text-neutral-300 dark:hover:bg-neutral-800",
                      )}
                    >
                      {mode}
                    </button>
                  ))}
                </div>
                <label className="mt-3 block text-xs font-medium text-neutral-500">
                  document id
                </label>
                <input
                  value={docId}
                  onChange={(e) => setDocId(e.target.value)}
                  placeholder={writeMode === "create" ? "auto or custom id" : "required id"}
                  className="mt-1 w-full rounded-lg border border-neutral-200 bg-panel px-3 py-2 font-mono text-sm dark:border-neutral-700 dark:bg-panel-dark"
                  disabled={!selectedName}
                />
                <label className="mt-3 block text-xs font-medium text-neutral-500">
                  JSON data
                </label>
                <textarea
                  value={docJson}
                  onChange={(e) => setDocJson(e.target.value)}
                  spellCheck={false}
                  className="mt-1 h-64 w-full resize-y rounded-lg border border-neutral-200 bg-panel p-3 font-mono text-xs leading-5 outline-none dark:border-neutral-700 dark:bg-panel-dark"
                  disabled={!selectedName}
                />
                {docError && (
                  <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
                    {docError}
                  </div>
                )}
                <div className="mt-3 flex flex-wrap justify-between gap-2">
                  <Button
                    type="button"
                    variant="danger"
                    size="sm"
                    disabled={!selectedDoc || deleteDocument.isPending}
                    onClick={() => {
                      if (selectedDoc && window.confirm(`Delete ${selectedDoc.id}?`)) {
                        deleteDocument.mutate(selectedDoc);
                      }
                    }}
                  >
                    <Trash2 size={14} />
                    Delete
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    loading={writeDocument.isPending}
                    disabled={!selectedName}
                    onClick={() => writeDocument.mutate()}
                  >
                    <Save size={14} />
                    Save
                  </Button>
                </div>
              </section>

              <section
                className={classNames(
                  "rounded-lg bg-panel p-3 surface-shadow dark:bg-panel-dark",
                  activePage !== "rules" && "hidden",
                )}
              >
                <div className="mb-3 flex items-center gap-2">
                  <Globe2 size={15} className="text-neutral-400" />
                  <h2 className="text-sm font-semibold">Rules</h2>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <RuleSelect label="read" value={readRule} onChange={setReadRule} />
                  <RuleSelect label="write" value={writeRule} onChange={setWriteRule} />
                </div>
                {publicPath && (
                  <button
                    type="button"
                    onClick={() => {
                      void navigator.clipboard.writeText(publicPath);
                      setCopiedPath(true);
                      window.setTimeout(() => setCopiedPath(false), 1200);
                    }}
                    className="mt-3 flex min-h-10 w-full items-center justify-between gap-2 rounded-lg bg-panel px-3 py-2 text-left font-mono text-xs text-neutral-600 transition-[background-color,color,scale] duration-150 ease-out hover:bg-neutral-100 active:scale-[0.96] dark:bg-panel-dark dark:text-neutral-300 dark:hover:bg-neutral-800"
                  >
                    <span className="truncate">{publicPath}</span>
                    {copiedPath ? <Check size={14} /> : <Copy size={14} />}
                  </button>
                )}
                {rulesError && (
                  <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
                    {rulesError}
                  </div>
                )}
                <Button
                  type="button"
                  size="sm"
                  className="mt-3 w-full"
                  loading={saveRules.isPending}
                  disabled={!selectedName}
                  onClick={() => saveRules.mutate()}
                >
                  <Save size={14} />
                  Save rules
                </Button>
              </section>

              <section
                className={classNames(
                  "rounded-lg bg-panel p-3 surface-shadow dark:bg-panel-dark",
                  activePage !== "rules" && "hidden",
                )}
              >
                <div className="mb-3 flex items-center gap-2">
                  <Braces size={15} className="text-neutral-400" />
                  <h2 className="text-sm font-semibold">Public write validator</h2>
                </div>
                <textarea
                  value={validatorText}
                  onChange={(e) => setValidatorText(e.target.value)}
                  spellCheck={false}
                  className="h-44 w-full resize-y rounded-lg border border-neutral-200 bg-panel p-3 font-mono text-xs leading-5 outline-none dark:border-neutral-700 dark:bg-panel-dark"
                  disabled={!selectedName}
                />
                {validatorError && (
                  <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
                    {validatorError}
                  </div>
                )}
                <div className="mt-3 flex gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="flex-1"
                    loading={clearValidator.isPending}
                    disabled={!selectedName}
                    onClick={() => clearValidator.mutate()}
                  >
                    Clear
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    className="flex-1"
                    loading={saveValidator.isPending}
                    disabled={!selectedName}
                    onClick={() => saveValidator.mutate()}
                  >
                    Save
                  </Button>
                </div>
              </section>

              <section
                className={classNames(
                  "rounded-lg bg-panel p-3 surface-shadow dark:bg-panel-dark",
                  activePage !== "keys" && "hidden",
                )}
              >
                <div className="mb-3 flex items-center gap-2">
                  <KeyRound size={15} className="text-neutral-400" />
                  <div>
                    <h2 className="text-sm font-semibold">Publishable DB keys</h2>
                    <p className="text-xs text-neutral-500">
                      Safe for frontend env. Access still depends on public collection rules.
                    </p>
                  </div>
                </div>
                <form
                  className="flex flex-wrap gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    createPublicKey.mutate(newPublicKeyLabel.trim());
                  }}
                >
                  <input
                    value={newPublicKeyLabel}
                    onChange={(e) => setNewPublicKeyLabel(e.target.value)}
                    placeholder="website, landing-page, docs"
                    maxLength={64}
                    className="min-w-[14rem] flex-1 rounded-lg border border-neutral-200 bg-bg px-3 py-2 text-sm dark:border-neutral-700 dark:bg-bg-dark"
                  />
                  <Button
                    type="submit"
                    size="sm"
                    loading={createPublicKey.isPending}
                  >
                    <Plus size={14} />
                    Create key
                  </Button>
                </form>
                {createPublicKey.isError && (
                  <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
                    {errorText(createPublicKey.error)}
                  </div>
                )}
                {copiedKey && (
                  <div className="mt-3 rounded-lg bg-emerald-50 p-3 text-xs text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200">
                    <div className="mb-2 flex items-center gap-1.5 font-semibold">
                      <Check size={14} />
                      Publishable key created
                    </div>
                    <div className="flex items-center gap-2">
                      <code className="min-w-0 flex-1 truncate rounded-md bg-white px-2 py-1.5 font-mono dark:bg-neutral-950">
                        {copiedKey}
                      </code>
                      <button
                        type="button"
                        onClick={() => void navigator.clipboard.writeText(copiedKey)}
                        className="inline-flex h-10 w-10 items-center justify-center rounded-lg bg-emerald-600 text-white transition-[background-color,scale] duration-150 ease-out hover:bg-emerald-700 active:scale-[0.96]"
                        aria-label="Copy publishable key"
                        title="Copy"
                      >
                        <Copy size={14} />
                      </button>
                    </div>
                  </div>
                )}
                <div className="mt-3 rounded-lg bg-bg p-3 text-xs text-neutral-600 dark:bg-bg-dark dark:text-neutral-300">
                  <div className="font-medium text-neutral-900 dark:text-neutral-100">
                    Frontend usage
                  </div>
                  <pre className="mt-2 overflow-x-auto whitespace-pre rounded-md bg-neutral-950 p-3 font-mono text-[11px] leading-5 text-neutral-100">
{`VITE_LCLOUD_ENDPOINT=https://your-lcloud-host
VITE_LCLOUD_DB_KEY=lcpk_...

const db = createBrowserClient({
  endpoint: import.meta.env.VITE_LCLOUD_ENDPOINT,
  publishableKey: import.meta.env.VITE_LCLOUD_DB_KEY,
});
await db.collection("posts").list({ limit: 20 });`}
                  </pre>
                </div>
                <div className="mt-3 space-y-2">
                  {publicKeysQ.isLoading && (
                    <div className="text-sm text-neutral-500">…</div>
                  )}
                  {publicKeysQ.data?.length === 0 && (
                    <div className="rounded-lg bg-bg px-3 py-6 text-center text-sm text-neutral-400 dark:bg-bg-dark">
                      No publishable DB keys yet.
                    </div>
                  )}
                  {publicKeysQ.data?.map((row) => (
                    <PublicKeyRow
                      key={row.id}
                      row={row}
                      onCopy={() => {
                        void navigator.clipboard.writeText(row.key);
                        setCopiedKey(row.key);
                      }}
                      onRevoke={() => {
                        if (window.confirm(`Revoke ${row.prefix}…?`)) {
                          revokePublicKey.mutate(row.id);
                        }
                      }}
                    />
                  ))}
                </div>

                <div className="mt-6 border-t border-neutral-200 pt-4 dark:border-neutral-800">
                  <div className="mb-3 flex items-center gap-2">
                    <UploadCloud size={15} className="text-neutral-400" />
                    <div>
                      <h2 className="text-sm font-semibold">Publishable storage keys</h2>
                      <p className="text-xs text-neutral-500">
                        For browser media uploads. Scoped to one cloud with explicit limits.
                      </p>
                    </div>
                  </div>
                  <form
                    className="grid gap-2 md:grid-cols-[1fr_1fr_auto]"
                    onSubmit={(e) => {
                      e.preventDefault();
                      createStorageKey.mutate();
                    }}
                  >
                    <select
                      value={selectedStorageCloudId}
                      onChange={(e) => setSelectedStorageCloudId(e.target.value)}
                      className="rounded-lg border border-neutral-200 bg-bg px-3 py-2 text-sm dark:border-neutral-700 dark:bg-bg-dark"
                    >
                      <option value="">Select media cloud</option>
                      {cloudsQ.data?.map((cloud) => (
                        <option key={cloud.id} value={cloud.id}>
                          {cloud.name} #{cloud.id}
                        </option>
                      ))}
                    </select>
                    <input
                      value={newStorageKeyLabel}
                      onChange={(e) => setNewStorageKeyLabel(e.target.value)}
                      placeholder="website media"
                      maxLength={64}
                      className="rounded-lg border border-neutral-200 bg-bg px-3 py-2 text-sm dark:border-neutral-700 dark:bg-bg-dark"
                    />
                    <Button type="submit" size="sm" loading={createStorageKey.isPending}>
                      <Plus size={14} />
                      Create storage key
                    </Button>
                    <input
                      value={storageMaxBytes}
                      onChange={(e) => setStorageMaxBytes(e.target.value)}
                      inputMode="numeric"
                      placeholder="max bytes"
                      className="rounded-lg border border-neutral-200 bg-bg px-3 py-2 text-sm dark:border-neutral-700 dark:bg-bg-dark"
                    />
                    <div className="flex flex-wrap items-center gap-3 rounded-lg bg-bg px-3 py-2 text-xs dark:bg-bg-dark md:col-span-2">
                      <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={storageAllowUpload} onChange={(e) => setStorageAllowUpload(e.target.checked)} /> upload</label>
                      <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={storageAllowList} onChange={(e) => setStorageAllowList(e.target.checked)} /> list</label>
                      <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={storageAllowDownload} onChange={(e) => setStorageAllowDownload(e.target.checked)} /> download</label>
                      <label className="inline-flex items-center gap-1.5"><input type="checkbox" checked={storageAllowDelete} onChange={(e) => setStorageAllowDelete(e.target.checked)} /> delete</label>
                    </div>
                  </form>
                  {createStorageKey.isError && (
                    <div className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">
                      {errorText(createStorageKey.error)}
                    </div>
                  )}
                  <div className="mt-3 rounded-lg bg-bg p-3 text-xs text-neutral-600 dark:bg-bg-dark dark:text-neutral-300">
                    <div className="font-medium text-neutral-900 dark:text-neutral-100">
                      Media frontend usage
                    </div>
                    <pre className="mt-2 overflow-x-auto whitespace-pre rounded-md bg-neutral-950 p-3 font-mono text-[11px] leading-5 text-neutral-100">
{`VITE_LCLOUD_STORAGE_KEY=lstore_...

const lcloud = createBrowserClient({
  endpoint: import.meta.env.VITE_LCLOUD_ENDPOINT,
  publishableKey: import.meta.env.VITE_LCLOUD_DB_KEY,
  storageKey: import.meta.env.VITE_LCLOUD_STORAGE_KEY,
});
const file = await lcloud.storage().upload(input.files[0]);
await lcloud.collection("posts").insert({ file_id: file.id, url: lcloud.storage().downloadUrl(file.id) });`}
                    </pre>
                  </div>
                  <div className="mt-3 space-y-2">
                    {storageKeysQ.isLoading && <div className="text-sm text-neutral-500">…</div>}
                    {storageKeysQ.data?.length === 0 && (
                      <div className="rounded-lg bg-bg px-3 py-6 text-center text-sm text-neutral-400 dark:bg-bg-dark">
                        No publishable storage keys yet.
                      </div>
                    )}
                    {storageKeysQ.data?.map((row) => (
                      <StorageKeyRow
                        key={row.id}
                        row={row}
                        cloudName={cloudsQ.data?.find((cloud) => cloud.id === row.cloud_id)?.name}
                        onCopy={() => {
                          void navigator.clipboard.writeText(row.key);
                          setCopiedKey(row.key);
                        }}
                        onRevoke={() => {
                          if (window.confirm(`Revoke ${row.prefix}…?`)) {
                            revokeStorageKey.mutate(row.id);
                          }
                        }}
                      />
                    ))}
                  </div>
                </div>
              </section>

              <section
                className={classNames(
                  "rounded-lg bg-panel p-3 surface-shadow dark:bg-panel-dark",
                  activePage !== "events" && "hidden",
                )}
              >
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Activity size={15} className="text-emerald-600 dark:text-emerald-400" />
                    <h2 className="text-sm font-semibold">Realtime events</h2>
                  </div>
                  <span className="text-xs tabular-nums text-neutral-500">
                    #{lastEventId}
                  </span>
                </div>
                <div className="space-y-2">
                  {events.length === 0 && (
                    <div className="rounded-lg bg-panel px-3 py-6 text-center text-xs text-neutral-400 dark:bg-panel-dark">
                      SSE подключён. Новые изменения появятся здесь.
                    </div>
                  )}
                  {events.map((event) => (
                    <div
                      key={`${event.id}-${event.op}-${event.doc_id ?? "collection"}`}
                      className="rounded-lg bg-panel px-3 py-2 text-xs dark:bg-panel-dark"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono font-semibold text-neutral-800 dark:text-neutral-100">
                          {event.op}
                        </span>
                        <span className="tabular-nums text-neutral-400">#{event.id}</span>
                      </div>
                      <div className="mt-1 truncate font-mono text-neutral-500">
                        {event.doc_id ?? "collection"}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

function CollectionButton({
  collection,
  active,
  onSelect,
}: {
  collection: JsonCollectionRow;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={classNames(
        "mb-1 flex min-h-12 w-full items-center gap-2 rounded-lg px-3 py-2 text-left transition-[background-color,color,scale] duration-150 ease-out active:scale-[0.96]",
        active
          ? "bg-neutral-100 dark:bg-neutral-800"
          : "hover:bg-neutral-50 dark:hover:bg-neutral-900",
      )}
    >
      <Database size={15} className="shrink-0 text-neutral-400" />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{collection.name}</span>
        <span className="mt-0.5 flex items-center gap-1 text-xs text-neutral-500">
          <span>{collection.read_rule}</span>
          <span aria-hidden="true">/</span>
          <span>{collection.write_rule}</span>
        </span>
      </span>
    </button>
  );
}

function PublicKeyRow({
  row,
  onCopy,
  onRevoke,
}: {
  row: JsonDbPublicKeyRow;
  onCopy: () => void;
  onRevoke: () => void;
}) {
  const revoked = row.revoked_at !== null;
  return (
    <div
      className={classNames(
        "flex items-center gap-3 rounded-lg bg-bg px-3 py-2 text-sm dark:bg-bg-dark",
        revoked && "opacity-50",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <code className="truncate font-mono">{row.key}</code>
          {revoked && (
            <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-500 dark:bg-neutral-800">
              revoked
            </span>
          )}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-2 text-xs text-neutral-500">
          {row.label && <span>{row.label}</span>}
          <span className="tabular-nums">created {formatDate(row.created_at)}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={onCopy}
        className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-neutral-500 transition-[background-color,color,scale] duration-150 ease-out hover:bg-neutral-100 hover:text-blue-600 active:scale-[0.96] dark:hover:bg-neutral-800"
        aria-label="Copy publishable key"
        title="Copy"
      >
        <Copy size={14} />
      </button>
      {!revoked && (
        <button
          type="button"
          onClick={onRevoke}
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-neutral-500 transition-[background-color,color,scale] duration-150 ease-out hover:bg-red-50 hover:text-red-600 active:scale-[0.96] dark:hover:bg-red-950/30"
          aria-label="Revoke publishable key"
          title="Revoke"
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  );
}

function StorageKeyRow({
  row,
  cloudName,
  onCopy,
  onRevoke,
}: {
  row: JsonStoragePublicKeyRow;
  cloudName?: string;
  onCopy: () => void;
  onRevoke: () => void;
}) {
  const revoked = row.revoked_at !== null;
  const permissions = [
    row.allow_upload && "upload",
    row.allow_list && "list",
    row.allow_download && "download",
    row.allow_delete && "delete",
  ].filter(Boolean).join(" / ");
  return (
    <div
      className={classNames(
        "flex items-center gap-3 rounded-lg bg-bg px-3 py-2 text-sm dark:bg-bg-dark",
        revoked && "opacity-50",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2">
          <code className="truncate font-mono">{row.key}</code>
          {revoked && (
            <span className="rounded bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-500 dark:bg-neutral-800">
              revoked
            </span>
          )}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-2 text-xs text-neutral-500">
          {row.label && <span>{row.label}</span>}
          <span>{cloudName ?? `cloud #${row.cloud_id}`}</span>
          <span>{permissions}</span>
          {row.max_file_bytes && <span className="tabular-nums">max {row.max_file_bytes} bytes</span>}
          <span className="tabular-nums">created {formatDate(row.created_at)}</span>
        </div>
      </div>
      <button
        type="button"
        onClick={onCopy}
        className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-neutral-500 transition-[background-color,color,scale] duration-150 ease-out hover:bg-neutral-100 hover:text-blue-600 active:scale-[0.96] dark:hover:bg-neutral-800"
        aria-label="Copy storage key"
        title="Copy"
      >
        <Copy size={14} />
      </button>
      {!revoked && (
        <button
          type="button"
          onClick={onRevoke}
          className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-neutral-500 transition-[background-color,color,scale] duration-150 ease-out hover:bg-red-50 hover:text-red-600 active:scale-[0.96] dark:hover:bg-red-950/30"
          aria-label="Revoke storage key"
          title="Revoke"
        >
          <Trash2 size={14} />
        </button>
      )}
    </div>
  );
}

function DocumentRow({
  row,
  active,
  onOpen,
}: {
  row: JsonDocumentRow;
  active: boolean;
  onOpen: () => void;
}) {
  const summary = prettyJson(row.data);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={classNames(
        "rounded-lg bg-panel p-3 text-left surface-shadow surface-shadow-hover active:scale-[0.99] dark:bg-panel-dark",
        active && "ring-2 ring-blue-500/60",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-mono text-sm font-semibold">{row.id}</div>
          <div className="mt-1 text-xs text-neutral-500">
            v<span className="tabular-nums">{row.version}</span> · {formatDate(row.updated_at)}
          </div>
        </div>
        <FileJson size={16} className="mt-0.5 shrink-0 text-neutral-400" />
      </div>
      <pre className="mt-3 line-clamp-4 overflow-hidden whitespace-pre-wrap break-words rounded-md bg-neutral-50 p-2 font-mono text-xs leading-5 text-neutral-600 dark:bg-neutral-900 dark:text-neutral-300">
        {summary}
      </pre>
    </button>
  );
}

function RuleSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: JsonAccessRule;
  onChange: (value: JsonAccessRule) => void;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium text-neutral-500">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as JsonAccessRule)}
        className="mt-1 h-10 w-full rounded-lg border border-neutral-200 bg-panel px-2 text-sm dark:border-neutral-700 dark:bg-panel-dark"
      >
        {RULES.map((rule) => (
          <option key={rule.value} value={rule.value}>
            {rule.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function EmptyState({
  icon: Icon,
  title,
  text,
}: {
  icon: LucideIcon;
  title: string;
  text: string;
}) {
  return (
    <div className="flex h-full min-h-64 flex-col items-center justify-center px-6 text-center">
      <Icon size={34} className="mb-3 text-neutral-300 dark:text-neutral-700" />
      <div className="text-sm font-medium">{title}</div>
      <p className="mt-1 max-w-sm text-sm text-neutral-500">{text}</p>
    </div>
  );
}
