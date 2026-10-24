/**
 * Request/response types for the Atlas AI API (hand-written from docs/openapi.json and
 * backend/app/schemas). Free-form backend dicts are typed as far as the backend guarantees.
 */

export type Uuid = string;
export type IsoDate = string;

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    request_id: string | null;
    details?: Array<Record<string, unknown>>;
  };
}

// ---- Auth -------------------------------------------------------------------------------

export type Role = "viewer" | "member" | "admin" | "owner";

export interface Me {
  user_id: Uuid;
  tenant_id: Uuid;
  email: string;
  role: Role;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: Me;
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name: string;
  workspace_name: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

// ---- Dashboard --------------------------------------------------------------------------

export interface Dashboard {
  collections: number;
  documents: number;
  documents_by_status: Partial<Record<DocumentStatus, number>>;
  chunks: number;
  conversations: number;
  runs_last_7_days: Partial<Record<RunOutcome | "unknown", number>>;
  recent_conversations: Array<{ id: Uuid; title: string; last_message_at: IsoDate }>;
  recent_documents: Array<{
    id: Uuid;
    title: string;
    status: DocumentStatus;
    collection_id: Uuid;
    chunk_count: number;
    updated_at: IsoDate;
  }>;
  recent_activity: Array<{
    action: string;
    resource_type: string | null;
    resource_id: string | null;
    created_at: IsoDate;
    details: { title?: string; name?: string; source_type?: string };
  }>;
}

// ---- Knowledge --------------------------------------------------------------------------

export interface Collection {
  id: Uuid;
  name: string;
  description: string | null;
  created_at: IsoDate;
  updated_at: IsoDate;
  document_count: number;
  ready_document_count: number;
  chunk_count: number;
  active_embedding_model: string | null;
}

export interface CollectionCreate {
  name: string;
  description?: string | null;
}

export type CollectionUpdate = Partial<CollectionCreate>;

export type DocumentStatus = "pending" | "processing" | "ready" | "failed" | "unsupported";
export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export type SourceType = "upload" | "url" | "text";

export interface DocumentOut {
  id: Uuid;
  collection_id: Uuid;
  title: string;
  source_type: SourceType;
  source_uri: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  content_hash: string;
  status: DocumentStatus;
  error_message: string | null;
  page_count: number | null;
  chunk_count: number;
  metadata: Record<string, unknown>;
  created_at: IsoDate;
  updated_at: IsoDate;
}

export interface IngestionJob {
  id: Uuid;
  status: JobStatus;
  attempt: number;
  error_code: string | null;
  error_message: string | null;
  started_at: IsoDate | null;
  finished_at: IsoDate | null;
  created_at: IsoDate;
}

export interface DocumentDetail extends DocumentOut {
  jobs: IngestionJob[];
}

export interface TextDocumentCreate {
  collection_id: Uuid;
  title: string;
  text: string;
  format: "text" | "markdown";
}

export interface UrlDocumentCreate {
  collection_id: Uuid;
  url: string;
  title?: string;
}

export interface Chunk {
  id: Uuid;
  document_id: Uuid;
  ordinal: number;
  text: string;
  page_number: number | null;
  section: string | null;
  token_count: number | null;
}

// ---- Chat -------------------------------------------------------------------------------

export type RunOutcome = "answered" | "abstained" | "clarification" | "error";
export type MessageRole = "user" | "assistant";

export interface Citation {
  marker: string;
  chunk_id: string;
  document_id: string;
  document_title: string;
  page_number?: number | null;
  section?: string | null;
  source_uri?: string | null;
  snippet?: string | null;
  source_deleted?: boolean;
}

export interface Claim {
  text: string;
  supported: boolean;
  markers: string[];
}

/** A retrieved passage. Live responses include `text`; stored history references omit it. */
export interface Evidence {
  chunk_id: string;
  document_id?: string;
  collection_id?: string;
  document_title?: string;
  source_uri?: string | null;
  text?: string;
  ordinal?: number;
  page_number?: number | null;
  section?: string | null;
  rank?: number | null;
  dense_score?: number | null;
  dense_rank?: number | null;
  lexical_score?: number | null;
  lexical_rank?: number | null;
  fused_score?: number | null;
  rerank_score?: number | null;
  suspicious?: boolean;
}

export interface RunErrorInfo {
  category?: string;
  message?: string;
  node?: string;
}

export interface MessageMetadata {
  outcome?: RunOutcome;
  claims?: Claim[];
  provider?: string | null;
  model?: string | null;
  error?: RunErrorInfo | null;
  evidence?: Evidence[];
}

export interface Message {
  id: Uuid;
  role: MessageRole;
  content: string;
  citations: Citation[];
  metadata: MessageMetadata;
  run_id: Uuid | null;
  created_at: IsoDate;
  feedback: number | null;
}

export interface RunSummary {
  id: Uuid | null;
  outcome: RunOutcome | null;
  provider: string | null;
  model: string | null;
  latency_ms: number | null;
  retrieval_attempts: number;
  llm_calls: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  error?: RunErrorInfo | null;
}

export interface ChatRequest {
  message: string;
  conversation_id?: Uuid | null;
  collection_ids?: Uuid[] | null;
}

export interface ChatResponse {
  conversation_id: Uuid;
  user_message_id: Uuid;
  message: Message;
  run: RunSummary;
  evidence: Evidence[];
}

export interface Conversation {
  id: Uuid;
  title: string;
  collection_id: Uuid | null;
  created_at: IsoDate;
  updated_at: IsoDate;
  last_message_at: IsoDate | null;
  message_count: number;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface FeedbackCreate {
  message_id: Uuid;
  rating: 1 | -1;
  comment?: string | null;
}

export interface FeedbackOut {
  id: Uuid;
  message_id: Uuid;
  rating: number;
  comment: string | null;
  created_at: IsoDate;
}

// ---- SSE events -------------------------------------------------------------------------

export type StreamEvent =
  | { event: "meta"; data: { conversation_id: Uuid; user_message_id: Uuid } }
  | { event: "step"; data: { node: string; status: string; latency_ms?: number } }
  | { event: "token"; data: { text: string } }
  | { event: "token_reset"; data: Record<string, unknown> }
  | { event: "final"; data: ChatResponse }
  | { event: "error"; data: { code: string; message: string } };

// ---- Runs -------------------------------------------------------------------------------

export interface Run {
  id: Uuid;
  conversation_id: Uuid | null;
  request_id: string | null;
  provider: string | null;
  model: string | null;
  outcome: RunOutcome | null;
  error_category: string | null;
  latency_ms: number | null;
  retrieval_attempts: number;
  generation_calls: number;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  created_at: IsoDate;
}

export interface NodeTraceEntry {
  node: string;
  status: string;
  latency_ms?: number;
  error_category?: string;
  [extra: string]: unknown;
}

export interface Sufficiency {
  sufficient?: boolean;
  reason?: string;
  top_dense_similarity?: number | null;
  top_lexical_coverage?: number | null;
  top_rerank_score?: number | null;
}

export interface RetrievalAttemptScore {
  chunk_id?: string;
  rank?: number | null;
  dense?: number | null;
  lexical?: number | null;
  fused?: number | null;
  rerank?: number | null;
  [extra: string]: unknown;
}

export interface RetrievalAttempt {
  attempt?: number;
  query?: string;
  mode?: string;
  results?: number;
  chunk_ids?: string[];
  scores?: RetrievalAttemptScore[];
  sufficiency?: Sufficiency;
  timings_ms?: Record<string, number>;
  warnings?: string[];
  embedding_model?: string | null;
  reranker?: string | null;
}

export interface RunDetail extends Run {
  retrieval_config: Record<string, unknown>;
  node_trace: NodeTraceEntry[];
  retrieved_chunk_ids: string[];
  details: {
    retrieval_attempts?: RetrievalAttempt[];
    final_sufficiency?: Sufficiency;
    suspicious_chunk_ids?: string[];
    action?: string | null;
    route_source?: string | null;
    claims_total?: number;
    claims_supported?: number;
    error?: RunErrorInfo | null;
  };
}

// ---- Settings ---------------------------------------------------------------------------

export type RetrievalMode = "dense" | "lexical" | "hybrid" | "hybrid_rerank";
export const RETRIEVAL_MODES: RetrievalMode[] = ["dense", "lexical", "hybrid", "hybrid_rerank"];

export interface WorkspaceSettings {
  llm_provider: string;
  llm_model: string;
  retrieval_mode: RetrievalMode;
  top_k: number;
  chunk_size: number;
  chunk_overlap: number;
  min_dense_similarity: number;
  evaluation_dataset: string;
}

export type WorkspaceSettingsUpdate = {
  llm_provider?: "groq" | "nvidia" | "gemini" | "openrouter" | "mock" | null;
  llm_model?: string | null;
  retrieval_mode?: RetrievalMode | null;
  top_k?: number | null;
  chunk_size?: number | null;
  chunk_overlap?: number | null;
  min_dense_similarity?: number | null;
  evaluation_dataset?: string | null;
};

export interface ProviderStatus {
  name: string;
  configured: boolean;
  capabilities: string[];
}

export interface SystemInfo {
  environment: string;
  demo_mode: boolean;
  default_llm_provider: string;
  default_llm_model: string;
  fallback_chain: string[];
  embedding_provider: string;
  embedding_model: string;
  embedding_dimension: number;
  reranker_provider: string;
  reranker_model: string | null;
  providers: ProviderStatus[];
  max_upload_bytes: number;
  agent_max_retrieval_attempts: number;
  agent_max_llm_calls: number;
}

// ---- Evaluations (contract shared with the backend; built in parallel) -----------------

export type EvaluationStatus = "queued" | "running" | "succeeded" | "failed";

export interface RetrievalMetrics {
  "recall@k"?: number | null;
  "precision@k"?: number | null;
  mrr?: number | null;
  "hit_rate@k"?: number | null;
  "ndcg@k"?: number | null;
}

export interface AnswerMetrics {
  correctness?: number | null;
  citation_precision?: number | null;
  faithfulness?: number | null;
  abstention_accuracy?: number | null;
  completeness?: number | null;
}

export interface EvaluationMetrics {
  case_count?: number;
  /** Cases with relevance labels (unanswerable cases have none and get null retrieval metrics). */
  labelled_case_count?: number;
  retrieval?: RetrievalMetrics;
  answer?: AnswerMetrics | null;
  latency_ms?: {
    retrieval_p50?: number | null;
    retrieval_p95?: number | null;
    end_to_end_p50?: number | null;
    end_to_end_p95?: number | null;
  };
  error_rate?: number | null;
  by_category?: Record<string, { "recall@k"?: number | null; mrr?: number | null; count?: number }>;
}

export interface EvaluationRun {
  id: Uuid;
  dataset_name: string;
  dataset_version: string | null;
  retrieval_mode: RetrievalMode | string;
  status: EvaluationStatus;
  config: {
    top_k?: number;
    provider?: string | null;
    model?: string | null;
    embedding_model?: string | null;
    reranker?: string | null;
    include_answers?: boolean;
    [extra: string]: unknown;
  };
  metrics: EvaluationMetrics | null;
  error_message: string | null;
  created_at: IsoDate;
  started_at: IsoDate | null;
  finished_at: IsoDate | null;
}

export interface EvaluationCase {
  case_id: string;
  category: string | null;
  question: string;
  retrieved_chunk_ids: string[];
  retrieval_metrics: RetrievalMetrics | null;
  answer_metrics: AnswerMetrics | null;
  answer: string | null;
  latency_ms: number | null;
  error_message: string | null;
}

export interface EvaluationRunDetail extends EvaluationRun {
  cases: EvaluationCase[];
}

export interface EvaluationDataset {
  name: string;
  version: string;
  case_count: number;
  categories: Record<string, number>;
  document_count: number;
  description?: string;
}

export interface EvaluationRunRequest {
  dataset?: string;
  modes?: RetrievalMode[];
  include_answers?: boolean;
  top_k?: number;
}

export interface EvaluationFilters {
  dataset?: string;
  mode?: string;
  status?: string;
  limit?: number;
  offset?: number;
}
