export interface User { about: string; id: number; login: string; name: string; language: string; theme: string; is_admin: boolean; show_thinking: boolean; active_profile_id: number | null; }
export interface ProfileLite { id: number; name: string; model: string; thinking_mode: string; thinking_depth: string | null; }
export interface Bootstrap { rag_folders: { id: number; name: string }[]; forms: string[]; vote_policies: string[]; version: string; profiles: ProfileLite[]; }
export interface Member { id: number; name: string; description: string; role: string; role_id: number; user_prompt: string; temperature: number;
  max_tokens: number; order: number; avatar_color: string; enabled: boolean; character_id: number | null; api_profile_id: number | null; profile_fallback: boolean; tools: string[] | null; rag_folder_ids: number[]; tokens: { tokens_in: number; tokens_out: number }; thinking_mode: string | null; thinking_depth: string | null; }
export interface ChecklistItem { ref: string; text: string; status: "open" | "done"; }
export interface Issue { ref: string; owner_member_id: number | null; text: string; satisfied_if: string; status: "open" | "resolved"; opened_round: number; resolved_round: number | null; }
export interface Evidence { ref: string; member_id: number | null; round_id: number | null; agent: string; query: string; digest: string; ok: boolean; created_at: string; }
export interface SymposiumSummary { id: number; title: string; topic: string; status: string; current_round: number; members: number; language: string; last_activity: string; mine: boolean; owner_login: string; }
export interface Hitl { id: number; member_id: number; question: string; options: string[]; context: string; status: string; }
export interface Detail { id: number; title: string; topic: string; completion_criterion: string; report_format: string; language: string; status: string;
  current_round: number; token_limit: number | null; max_rounds: number | null; form: string; vote_policy: string; end_reason: string | null;
  api_profile: { id: number; name: string; model: string } | null; ws_state: string; ws_reason: string; ws_mode: string; error_info: { type: string; message: string; recoverable?: boolean; retry_at?: string } | null;
  usage: { tokens_in: number; tokens_out: number }; pending_hitl: Hitl | null; rag_folders: { id: number; name: string }[]; members: Member[];
  checklist: ChecklistItem[]; issues: Issue[]; evidence_count: number; summary_mode: string;
  topic_full: string; fallback_profile_id: number | null; recovery_attempts: number; thinking_mode: string; thinking_depth: string | null; }
export interface StateBlock { action: string; reply_to: string[]; stance: Record<string, string>; claims: string[]; questions: string[]; evidence: string[]; checklist: Record<string, string>; issues: Record<string, string>; readiness: string; }
export interface Message { id: number; round_id: number | null; seq: number; kind: string; member_id: number | null; content: string; thinking: string | null;
  tokens_in: number; tokens_out: number; tokens_thinking: number; model: string | null; meta: any; created_at: string; }
export interface Round { id: number; number: number; status: string; summary: string | null; }
export interface LimitPolicy { enabled: boolean; max_concurrent: number | null; min_gap_ms: number; rps: number | null; rpm: number | null; rph: number | null; rpd: number | null;
  tpm: number | null; tpd: number | null; tokens_per_month: number | null; on_exhausted: "pause" | "wait"; respect_retry_after: boolean; }
export interface Profile { thinking_mode: string; thinking_depth: string | null; id: number; name: string; base_url: string; api_key_masked: string; model: string; limits: LimitPolicy; supports_thinking: boolean; extra_body: string; limits_usage: any; total_tokens: number; total_calls: number; }
export interface Preset { key: string; title: string; note: string; source: string; policy: LimitPolicy; }
export interface AvatarParams { hair?: string; hair_color?: string; face?: string; bg?: string; outfit?: string; }
export interface Character { id: number; key: string | null; is_default: boolean; mine: boolean; name: string; title: string; description: string; prompt: string;
  speech_style: string; avatar_seed: string; avatar_style: string; avatar_color: string; avatar: AvatarParams; api_profile_id: number | null; profile_fallback: boolean;
  temperature: number; max_tokens: number; thinking_mode: string | null; thinking_depth: string | null; tools: string[] | null; rag_folder_ids: number[]; subagent_limit: number | null; }
export interface RagFolder { id: number; name: string; description?: string }
