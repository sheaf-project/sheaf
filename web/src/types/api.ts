export type PrivacyLevel = "public" | "friends" | "private";
export type DeleteConfirmation = "none" | "password" | "totp" | "both";
export type DateFormat = "dmy" | "mdy" | "ymd";

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface User {
  id: string;
  email: string;
  totp_enabled: boolean;
  tier: string;
  is_admin: boolean;
  account_status: string;
  email_verified: boolean;
  created_at: string;
  last_login_at: string | null;
  deletion_requested_at: string | null;
  deletion_scheduled_for: string | null;
  newsletter_opt_in: boolean;
  email_delivery_status: "ok" | "soft_bouncing" | "hard_bounced" | "complained";
  email_revalidation_required: boolean;
  uploads_allowed: boolean;
  bio_uploads_allowed: boolean;
  external_images_allowed: boolean;
}

export interface ApiKey {
  id: string;
  name: string;
  scopes: string[];
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface ApiKeyCreated extends ApiKey {
  key: string;
}

export interface System {
  id: string;
  name: string;
  description: string | null;
  tag: string | null;
  avatar_url: string | null;
  color: string | null;
  privacy: PrivacyLevel;
  delete_confirmation: DeleteConfirmation;
  date_format: DateFormat;
  replace_fronts_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface SystemUpdate {
  name?: string;
  description?: string | null;
  tag?: string | null;
  avatar_url?: string | null;
  color?: string | null;
  privacy?: PrivacyLevel;
  date_format?: DateFormat;
  replace_fronts_default?: boolean;
}

export interface Member {
  id: string;
  system_id: string;
  name: string;
  display_name: string | null;
  description: string | null;
  pronouns: string | null;
  avatar_url: string | null;
  color: string | null;
  birthday: string | null;
  privacy: PrivacyLevel;
  created_at: string;
  updated_at: string;
}

export interface MemberCreate {
  name: string;
  display_name?: string | null;
  description?: string | null;
  pronouns?: string | null;
  avatar_url?: string | null;
  color?: string | null;
  birthday?: string | null;
  privacy?: PrivacyLevel;
}

export interface MemberUpdate {
  name?: string;
  display_name?: string | null;
  description?: string | null;
  pronouns?: string | null;
  avatar_url?: string | null;
  color?: string | null;
  birthday?: string | null;
  privacy?: PrivacyLevel;
}

export interface Front {
  id: string;
  system_id: string;
  started_at: string;
  ended_at: string | null;
  member_ids: string[];
}

export interface FrontCreate {
  member_ids: string[];
  started_at?: string | null;
  replace_fronts?: boolean;
}

export interface FrontUpdate {
  ended_at?: string | null;
  member_ids?: string[];
}

export interface Group {
  id: string;
  system_id: string;
  name: string;
  description: string | null;
  color: string | null;
  parent_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface GroupCreate {
  name: string;
  description?: string | null;
  color?: string | null;
  parent_id?: string | null;
}

export interface GroupUpdate {
  name?: string;
  description?: string | null;
  color?: string | null;
  parent_id?: string | null;
}

export interface Tag {
  id: string;
  system_id: string;
  name: string;
  color: string | null;
  created_at: string;
  updated_at: string;
}

export interface TagCreate {
  name: string;
  color?: string | null;
}

export interface TagUpdate {
  name?: string;
  color?: string | null;
}

export type FieldType = "text" | "number" | "date" | "boolean" | "select" | "multiselect";

export interface CustomField {
  id: string;
  system_id: string;
  name: string;
  field_type: FieldType;
  options: Record<string, unknown> | null;
  order: number;
  privacy: PrivacyLevel;
  created_at: string;
  updated_at: string;
}

export interface CustomFieldCreate {
  name: string;
  field_type: FieldType;
  options?: Record<string, unknown> | null;
  order?: number;
  privacy?: PrivacyLevel;
}

export interface CustomFieldUpdate {
  name?: string;
  options?: Record<string, unknown> | null;
  order?: number;
  privacy?: PrivacyLevel;
}

export interface CustomFieldValue {
  field_id: string;
  member_id: string;
  value: unknown;
}

export interface CustomFieldValueSet {
  field_id: string;
  value: unknown;
}

export type PendingActionType =
  | "member_delete"
  | "group_delete"
  | "tag_delete"
  | "field_delete"
  | "front_delete"
  | "journal_delete"
  | "image_delete";

export type PendingActionStatus =
  | "pending"
  | "cancelled"
  | "completed"
  | "errored";

export interface PendingAction {
  id: string;
  action_type: PendingActionType;
  target_id: string;
  target_label: string;
  requested_at: string;
  requested_by_user_id: string | null;
  finalize_after: string;
  fronting_member_ids: string[];
  fronting_member_names: string[];
  status: PendingActionStatus;
}

export type SafetyChangeStatus = "pending" | "cancelled" | "completed";

export interface SafetyChangeRequest {
  id: string;
  requested_at: string;
  requested_by_user_id: string | null;
  finalize_after: string;
  changes: Record<string, unknown>;
  status: SafetyChangeStatus;
}

// auth_tier is the historical `delete_confirmation` setting, repurposed
// as the auth tier for all safeguarded destructive actions.
export interface SystemSafetySettings {
  grace_period_days: number;
  auth_tier: DeleteConfirmation;
  applies_to_members: boolean;
  applies_to_groups: boolean;
  applies_to_tags: boolean;
  applies_to_fields: boolean;
  applies_to_fronts: boolean;
  applies_to_journals: boolean;
  applies_to_images: boolean;
}

export interface SystemSafetyUpdate {
  grace_period_days?: number;
  auth_tier?: DeleteConfirmation;
  applies_to_members?: boolean;
  applies_to_groups?: boolean;
  applies_to_tags?: boolean;
  applies_to_fields?: boolean;
  applies_to_fronts?: boolean;
  applies_to_journals?: boolean;
  applies_to_images?: boolean;
  password?: string;
  totp_code?: string;
}

export interface SystemSafetyResponse {
  settings: SystemSafetySettings;
  pending_actions: PendingAction[];
  pending_changes: SafetyChangeRequest[];
}

export interface SystemSafetyUpdateResponse {
  settings: SystemSafetySettings;
  applied: string[];
  deferred: string[];
  pending_change: SafetyChangeRequest | null;
}

export interface DeleteQueued {
  pending_action_id: string;
  finalize_after: string;
}

export type DeleteResult = void | DeleteQueued;

export function isDeleteQueued(r: DeleteResult): r is DeleteQueued {
  return !!r && typeof (r as DeleteQueued).pending_action_id === "string";
}

export interface DestructiveConfirm {
  password?: string;
  totp_code?: string;
}

// ---------------------------------------------------------------------------
// Journals + Revision History
// ---------------------------------------------------------------------------

export type JournalVisibility = "system" | "member_private" | "public";

export interface JournalEntry {
  id: string;
  system_id: string;
  member_id: string | null;
  title: string | null;
  body: string;
  visibility: JournalVisibility;
  author_user_id: string | null;
  author_member_ids: string[];
  author_member_names: string[];
  created_at: string;
  updated_at: string;
}

export interface JournalEntryWithCount extends JournalEntry {
  revision_count: number;
}

export interface JournalEntryCreate {
  member_id?: string | null;
  title?: string | null;
  body: string;
  visibility?: JournalVisibility;
  author_member_ids?: string[];
}

export interface JournalEntryUpdate {
  title?: string | null;
  body?: string;
  visibility?: JournalVisibility;
  author_member_ids?: string[];
}

export interface JournalListResponse {
  items: JournalEntry[];
  next_cursor: string | null;
}

export type ContentRevisionTarget = "journal_entry" | "member_bio";

export interface ContentRevision {
  id: string;
  target_type: ContentRevisionTarget;
  target_id: string;
  user_id: string | null;
  editor_member_ids: string[];
  editor_member_names: string[];
  title: string | null;
  body: string;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Revision retention
// ---------------------------------------------------------------------------

export type RetentionTrimStatus = "pending" | "cancelled" | "completed";

export interface RetentionTrimNotice {
  id: string;
  requested_at: string;
  effective_at: string;
  from_tier: string;
  to_tier: string;
  reason: string;
  status: RetentionTrimStatus;
}

// 0 = unlimited on either tier_max or override.
export interface RetentionSettings {
  effective_max_revisions: number;
  effective_max_days: number;
  tier_max_revisions: number;
  tier_max_days: number;
  override_revisions: number | null;
  override_days: number | null;
  trim_notice: RetentionTrimNotice | null;
}

export interface RetentionUpdate {
  max_revisions?: number | null;
  max_revision_days?: number | null;
  password?: string;
  totp_code?: string;
}
