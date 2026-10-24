/** Typed functions for every backend endpoint used by the UI. */
import { request } from "./client";
import type {
  ChatRequest,
  ChatResponse,
  Chunk,
  Collection,
  CollectionCreate,
  CollectionUpdate,
  Conversation,
  ConversationDetail,
  Dashboard,
  DocumentDetail,
  DocumentOut,
  DocumentStatus,
  EvaluationDataset,
  EvaluationFilters,
  EvaluationRun,
  EvaluationRunDetail,
  EvaluationRunRequest,
  FeedbackCreate,
  FeedbackOut,
  LoginRequest,
  Me,
  Page,
  RegisterRequest,
  Run,
  RunDetail,
  RunOutcome,
  SystemInfo,
  TextDocumentCreate,
  TokenResponse,
  UrlDocumentCreate,
  WorkspaceSettings,
  WorkspaceSettingsUpdate,
} from "./types";

const V1 = "/api/v1";

export interface PageParams {
  limit?: number;
  offset?: number;
}

export const api = {
  auth: {
    register: (body: RegisterRequest) =>
      request<TokenResponse>(`${V1}/auth/register`, { method: "POST", body, anonymous: true }),
    login: (body: LoginRequest) =>
      request<TokenResponse>(`${V1}/auth/login`, { method: "POST", body, anonymous: true }),
    me: () => request<Me>(`${V1}/auth/me`),
  },

  dashboard: () => request<Dashboard>(`${V1}/dashboard`),

  collections: {
    list: (params: PageParams = {}) =>
      request<Page<Collection>>(`${V1}/collections`, { query: { limit: 100, ...params } }),
    get: (id: string) => request<Collection>(`${V1}/collections/${id}`),
    create: (body: CollectionCreate) => request<Collection>(`${V1}/collections`, { method: "POST", body }),
    update: (id: string, body: CollectionUpdate) =>
      request<Collection>(`${V1}/collections/${id}`, { method: "PATCH", body }),
    remove: (id: string) => request<void>(`${V1}/collections/${id}`, { method: "DELETE" }),
    reembed: (id: string) =>
      request<{ status: string }>(`${V1}/collections/${id}/reembed`, { method: "POST" }),
  },

  documents: {
    list: (params: PageParams & { collection_id?: string; status?: DocumentStatus } = {}) =>
      request<Page<DocumentOut>>(`${V1}/documents`, { query: { ...params } }),
    get: (id: string) => request<DocumentDetail>(`${V1}/documents/${id}`),
    upload: (collectionId: string, file: File, title?: string) => {
      const form = new FormData();
      form.append("collection_id", collectionId);
      form.append("file", file);
      if (title) form.append("title", title);
      return request<DocumentOut>(`${V1}/documents/upload`, { method: "POST", body: form });
    },
    createText: (body: TextDocumentCreate) =>
      request<DocumentOut>(`${V1}/documents/text`, { method: "POST", body }),
    createUrl: (body: UrlDocumentCreate) =>
      request<DocumentOut>(`${V1}/documents/url`, { method: "POST", body }),
    retry: (id: string) => request<DocumentOut>(`${V1}/documents/${id}/retry`, { method: "POST" }),
    remove: (id: string) => request<void>(`${V1}/documents/${id}`, { method: "DELETE" }),
    chunks: (id: string, params: PageParams = {}) =>
      request<Page<Chunk>>(`${V1}/documents/${id}/chunks`, { query: { ...params } }),
  },

  chunks: {
    get: (id: string) => request<Chunk>(`${V1}/chunks/${id}`),
  },

  chat: {
    send: (body: ChatRequest) => request<ChatResponse>(`${V1}/chat`, { method: "POST", body }),
  },

  conversations: {
    list: (params: PageParams = {}) =>
      request<Page<Conversation>>(`${V1}/conversations`, { query: { limit: 50, ...params } }),
    get: (id: string) => request<ConversationDetail>(`${V1}/conversations/${id}`),
    rename: (id: string, title: string) =>
      request<Conversation>(`${V1}/conversations/${id}`, { method: "PATCH", body: { title } }),
    remove: (id: string) => request<void>(`${V1}/conversations/${id}`, { method: "DELETE" }),
  },

  feedback: {
    submit: (body: FeedbackCreate) => request<FeedbackOut>(`${V1}/feedback`, { method: "POST", body }),
  },

  runs: {
    list: (params: PageParams & { outcome?: RunOutcome } = {}) =>
      request<Page<Run>>(`${V1}/runs`, { query: { ...params } }),
    get: (id: string) => request<RunDetail>(`${V1}/runs/${id}`),
  },

  settings: {
    get: () => request<WorkspaceSettings>(`${V1}/settings`),
    update: (body: WorkspaceSettingsUpdate) =>
      request<WorkspaceSettings>(`${V1}/settings`, { method: "PUT", body }),
    system: () => request<SystemInfo>(`${V1}/settings/system`),
  },

  evaluations: {
    run: (body: EvaluationRunRequest) =>
      request<{ runs: EvaluationRun[] }>(`${V1}/evaluations/run`, { method: "POST", body }),
    list: (filters: EvaluationFilters = {}) =>
      request<Page<EvaluationRun>>(`${V1}/evaluations`, { query: { limit: 50, ...filters } }),
    get: (id: string) => request<EvaluationRunDetail>(`${V1}/evaluations/${id}`),
    datasets: () => request<EvaluationDataset[]>(`${V1}/evaluations/datasets`),
  },
};
