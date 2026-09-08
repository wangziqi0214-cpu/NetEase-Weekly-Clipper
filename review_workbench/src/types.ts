export type PlatformType = 'netease' | 'qq' | 'kkbox' | 'all';
export type ReviewStatus = 'pending' | 'approved' | 'rejected' | 'deferred' | 'machine_filtered';
export type ScreeningTier = 'TIER_1_HOT' | 'TIER_2_RULE' | 'TIER_3_ATTENTION' | 'TIER_4_NOISE';

export interface RawCandidateRecord {
  id: number;
  platform: string;
  source_id: string;
  title: string;
  artist_names: string;
  album_title: string;
  release_type: string;
  release_date: string;
  duration_ms: number;
  track_number: number;
  source_url: string;
  raw_metadata?: Record<string, unknown>;
  status: ReviewStatus;
  reviewed_at?: string | null;
}

export interface CandidateItem {
  id: number;
  canonical_key: string;
  title: string;
  clean_title: string;
  featured_artists: string[];
  artist_names: string;
  album_title: string;
  release_type: string;
  release_date: string;
  duration_ms: number;
  status: ReviewStatus;
  platform: string;
  platforms: string[];
  platforms_display: string;
  dedup_count: number;
  source_url: string;
  source_urls: Record<string, string>;
  raw_ids: number[];
  raw_candidates: RawCandidateRecord[];
  cover_url?: string;
  screening_tier: ScreeningTier;
  screening_reasons: string[];
  completeness_score: number;
  rule_applied?: string;
}

export interface DatabaseStats {
  total: number;
  reviewable_total: number;
  hidden_incomplete: number;
  unique_total: number;
  pending: number;
  unique_pending: number;
  approved: number;
  unique_approved: number;
  rejected: number;
  unique_rejected: number;
  deferred: number;
  unique_deferred: number;
  machine_filtered: number;
  unique_machine_filtered: number;
  by_platform: Record<string, number>;
  by_tier?: Record<string, number>;
}

export interface CandidatePage {
  items: CandidateItem[];
  count: number;
  raw_count: number;
  hidden_incomplete: number;
}

export interface PublicationItem {
  candidate_id: number;
  platform: string;
  original_track_id: string;
  netease_track_id?: string | null;
  track_title: string;
  artist_names: string;
  matched_title?: string;
  matched_artists?: string;
  match_status: 'direct' | 'matched' | 'ambiguous' | 'unmatched';
  match_confidence: number;
  is_resolved: boolean;
  is_published?: boolean;
  is_excluded_from_delivery?: boolean;
  source_release_date?: string;
  target_release_date?: string;
  effective_release_date?: string;
  release_gate_reason?: string;
  is_manual_override?: boolean;
  manual_override?: {
    candidate_id: number;
    netease_track_id: string;
    matched_title?: string;
    matched_artists?: string;
    target_release_date?: string;
    notes?: string;
    updated_at?: string;
  } | null;
  match_details?: {
    reasons?: string[];
    details?: any;
    attempted_queries?: string[];
    type?: string;
    notes?: string;
  };
}

export interface PublicationPreview {
  playlist_name: string;
  total_approved: number;
  ready_count: number;
  direct_netease_count: number;
  matched_count: number;
  ambiguous_count: number;
  unmatched_count: number;
  ready_items: PublicationItem[];
  ambiguous_items: PublicationItem[];
  unmatched_items: PublicationItem[];
  excluded_items?: PublicationItem[];
  excluded_count?: number;
  readiness: { is_ready: boolean; pending_count: number; incomplete_pending_count: number; message: string };
  ordered_candidate_ids: number[];
  out_of_week_count: number;
  out_of_week_items: PublicationItem[];
  release_window: {start:string;end:string};
}

export interface ArtistKnowledge {
  artist_name: string;
  display_name: string;
  factual_summary: string;
  sources: string[];
  uncertainty: 'low' | 'medium' | 'high' | string;
  identity_context: {
    genre?: string[] | string;
    origin?: string;
    type?: string;
    members?: string[];
    active_years?: string;
    notable_works?: string[];
    [key: string]: any;
  };
  status: 'pending' | 'collecting' | 'completed' | 'sparse' | 'failed' | 'not_found' | 'local_missing';
  updated_at?: string;
  error?: string;
}

export interface NetEaseSearchItem {
  id: string;
  title: string;
  artists: string;
  album: string;
  duration_ms: number;
  release_date: string;
  url: string;
}

export interface ManualMatchPayload {
  candidate_id: number;
  netease_track_id: string;
  matched_title?: string;
  matched_artists?: string;
  target_release_date?: string;
  notes?: string;
}

export interface VideoWorkflowJob {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  playlist_id?: string;
  playlist_url: string;
  publication_id?: string;
  ordered_track_ids?: string[];
  track_count?: number;
  stage?: 'queued' | 'running' | 'crawl' | 'research' | 'tts' | 'intro' | 'report' | 'rendering' | 'completed' | 'failed' | 'cancelled';
  stage_label?: string;
  log_path: string;
  pid?: number;
  error: string;
  created_at: string;
  updated_at: string;
  artifacts?: Record<string, string>;
}

export interface PublishCopyData {
  job_id: string;
  auto_text: string;
  editable_text: string;
  is_modified: boolean;
  has_auto: boolean;
  has_editable: boolean;
}

export interface LearningSafety {
  verdict: 'green' | 'locked';
  mode: 'active' | 'shadow_only';
  auto_filter_enabled: boolean;
  approved_unique: number;
  approved_missed_by_old_filter: number;
  historical_miss_rate: number;
  feedback_count: number;
  feedback_target: number;
  candidate_model: { version: string; recall: number; precision: number; activated: boolean };
  active_model: { version: string; recall?: number; precision?: number };
  gates: Record<string, boolean>;
}

export interface CandidateQueryParams {
  platform?: string;
  status?: string;
  tier?: string;
  keyword?: string;
  dedup?: boolean;
}
