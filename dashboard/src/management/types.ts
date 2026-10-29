export interface ItemList<T> {
  items: T[];
}

export interface SourceRoot {
  root_id: string;
  path: string;
  label?: string;
}

export interface BuildProfile {
  profile_id: string;
  label: string;
  adapter: string;
  embedding_provider: string;
  embedding_model: string;
}

export interface LegacyGeneration {
  generation_id: string;
  corpus_id: string;
  revision?: string;
  path?: string;
  embedding_model?: string;
  embedding_dimension?: number;
}

export interface ManagementConfig {
  enabled: boolean;
  storage_root: string;
  source_roots: SourceRoot[];
  profiles: BuildProfile[];
  max_selected_tags: number;
  legacy_generations?: LegacyGeneration[];
  worker_error?: string | null;
}

export interface SourceDirectory {
  name: string;
  path: string;
  is_repository: boolean;
}

export interface DirectoryListing extends ItemList<SourceDirectory> {
  root_id: string;
  path: string;
}

export interface Repository {
  repo_id: string;
  path: string;
  name: string;
}

export interface SourceTag {
  tag: string;
  tag_oid: string;
  commit: string;
  annotated: boolean;
  engine_version?: string | null;
}

export interface TagListing extends ItemList<SourceTag> {
  next_cursor: string | null;
  total: number;
}

export interface Database {
  database_id: string;
  name: string;
  repo_id?: string;
  profile_id: string;
  created_at: string;
}

export interface BuildPlanItem extends SourceTag {
  target_id: string;
}

export interface BuildPlan {
  plan_id: string;
  database_id: string;
  repo_id: string;
  items: BuildPlanItem[];
  created_at: string;
  warnings?: string[];
  summary?: {
    version_count: number;
    file_occurrences: number;
    logical_source_bytes: number;
    unique_source_bytes: number;
    shared_file_occurrences: number;
    embedding_estimate: number | null;
    notes: string[];
  };
}

export type JobStatus =
  | "queued" | "running" | "validating" | "publishing"
  | "cancel_requested" | "cancelled" | "failed" | "interrupted"
  | "succeeded" | "partial" | "partial_success";

export interface JobItem {
  tag: string;
  tag_oid: string;
  commit: string;
  engine_version?: string | null;
  target_id: string;
  snapshot_id?: string | null;
  status: JobStatus;
  phase: string;
  phase_started_at?: string;
  ended_at?: string;
  completed_units: number;
  total_units: number | null;
  unit: string;
  error?: string | Record<string, unknown> | null;
  statistics?: Record<string, number>;
  counts?: Record<string, number>;
  reused_vectors?: number;
  new_vectors?: number;
}

export interface BuildJob {
  job_id: string;
  database_id: string;
  repo_id: string;
  plan_id: string;
  status: JobStatus;
  phase: string;
  completed_units: number;
  total_units: number | null;
  unit: string;
  completed_tags: number;
  total_tags: number;
  created_at: string;
  updated_at: string;
  error?: string | Record<string, unknown> | null;
  items: JobItem[];
  statistics?: Record<string, number>;
}

export interface JobEvent {
  seq: number;
  job_id: string;
  at: string;
  type: string;
  payload: Record<string, unknown>;
}

export interface SpaceUsage {
  measured_at?: string;
  breakdown?: {
    source_bytes?: number | null;
    structure_bytes?: number | null;
    vector_bytes?: number | null;
    index_bytes?: number | null;
    mapping_bytes?: number | null;
    total_bytes?: number | null;
  };
  logical_source_bytes?: number | null;
  physical_source_bytes?: number | null;
  vector_count?: number | null;
  shared_bytes?: number | null;
  source_bytes?: number | null;
  structure_bytes?: number | null;
  vector_bytes?: number | null;
  index_bytes?: number | null;
  mapping_bytes?: number | null;
  total_bytes?: number | null;
  notes?: string[];
}
