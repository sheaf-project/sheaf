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
  /** Lightweight scratchpad note; deliberately overwrite-only and not
   *  protected by System Safety. ~5kb plaintext cap. */
  note: string | null;
  tag: string | null;
  avatar_url: string | null;
  color: string | null;
  privacy: PrivacyLevel;
  delete_confirmation: DeleteConfirmation;
  date_format: DateFormat;
  replace_fronts_default: boolean;
  coalesce_contiguous_fronts: boolean;
  created_at: string;
  updated_at: string;
}

export interface SystemUpdate {
  name?: string;
  description?: string | null;
  note?: string | null;
  tag?: string | null;
  avatar_url?: string | null;
  color?: string | null;
  privacy?: PrivacyLevel;
  date_format?: DateFormat;
  replace_fronts_default?: boolean;
  coalesce_contiguous_fronts?: boolean;
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
  pluralkit_id: string | null;
  emoji: string | null;
  is_custom_front: boolean;
  privacy: PrivacyLevel;
  /** Lightweight scratchpad note; deliberately overwrite-only and not
   *  protected by System Safety. ~5kb plaintext cap. */
  note: string | null;
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
  pluralkit_id?: string | null;
  emoji?: string | null;
  is_custom_front?: boolean;
  privacy?: PrivacyLevel;
  note?: string | null;
}

export interface MemberUpdate {
  name?: string;
  display_name?: string | null;
  description?: string | null;
  pronouns?: string | null;
  avatar_url?: string | null;
  color?: string | null;
  birthday?: string | null;
  pluralkit_id?: string | null;
  emoji?: string | null;
  is_custom_front?: boolean;
  privacy?: PrivacyLevel;
  note?: string | null;
}

export interface Front {
  id: string;
  system_id: string;
  started_at: string;
  ended_at: string | null;
  member_ids: string[];
  custom_status: string | null;
  // Per-member effective "fronting since" timestamp, keyed by member id.
  // For open fronts on /v1/fronts/current with the system's
  // coalesce_contiguous_fronts toggle on, this walks back through
  // contiguous front entries (each ending exactly when the next began)
  // so a member who went solo -> cofront keeps their original
  // fronting-since instead of resetting on the new entry. Closed-front
  // history endpoints always return the literal entry started_at here.
  member_since: Record<string, string>;
  // Members whose walk-back hit the safety depth cap. The corresponding
  // `member_since` entry is a lower bound, not the true chain start —
  // render with a "> X ago" prefix.
  member_since_capped: string[];
}

export interface FrontCreate {
  member_ids: string[];
  started_at?: string | null;
  replace_fronts?: boolean;
  custom_status?: string | null;
}

// --- Reminders -----------------------------------------------------------

export type ReminderTriggerType = "automated" | "repeated";
export type ReminderTriggerEvent = "start" | "stop" | "any";
export type ReminderScheduleKind = "daily" | "weekly" | "monthly";
export type ReminderScope = "system" | "member";

export interface Reminder {
  id: string;
  system_id: string;
  channel_id: string;
  name: string;
  title: string;
  body: string | null;
  enabled: boolean;
  trigger_type: ReminderTriggerType;
  trigger_member_id: string | null;
  trigger_event: ReminderTriggerEvent | null;
  delay_seconds: number | null;
  schedule_kind: ReminderScheduleKind | null;
  schedule_time: string | null;
  schedule_dow_mask: number | null;
  schedule_dom: number | null;
  schedule_tz: string | null;
  cron_expression: string | null;
  scope: ReminderScope;
  scope_member_ids: string[];
  digest_when_absent: boolean;
  last_fired_at: string | null;
  pending_count: number;
  next_fire_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReminderCreate {
  channel_id: string;
  name: string;
  title: string;
  body?: string | null;
  enabled?: boolean;
  trigger_type: ReminderTriggerType;
  trigger_member_id?: string | null;
  trigger_event?: ReminderTriggerEvent | null;
  delay_seconds?: number | null;
  schedule_kind?: ReminderScheduleKind | null;
  schedule_time?: string | null;
  schedule_dow_mask?: number | null;
  schedule_dom?: number | null;
  schedule_tz?: string | null;
  cron_expression?: string | null;
  scope?: ReminderScope;
  scope_member_ids?: string[];
  digest_when_absent?: boolean;
}

export type ReminderUpdate = Partial<ReminderCreate>;

export interface MemberFrontingStats {
  member_id: string;
  is_custom_front: boolean;
  total_seconds: number;
  percent_of_window: number;
  session_count: number;
  longest_session_seconds: number;
  hour_of_day_seconds: number[]; // 24 entries, indexed 0..23 in the requested tz
}

export interface FrontingAnalytics {
  since: string;
  until: string;
  tz: string;
  window_seconds: number;
  members: MemberFrontingStats[];
}

export interface FrontUpdate {
  ended_at?: string | null;
  member_ids?: string[];
  // Omit to keep, send null to clear, send a string to replace.
  custom_status?: string | null;
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
  | "image_delete"
  | "revision_unpin"
  | "watch_token_revoke"
  | "channel_delete";

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
  applies_to_revisions: boolean;
  applies_to_notifications: boolean;
  applies_to_reminders: boolean;
  applies_to_polls: boolean;
  applies_to_messages: boolean;
  auto_pin_first_revision: boolean;
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
  applies_to_revisions?: boolean;
  applies_to_notifications?: boolean;
  applies_to_reminders?: boolean;
  applies_to_polls?: boolean;
  applies_to_messages?: boolean;
  auto_pin_first_revision?: boolean;
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

export type ContentRevisionTarget = "journal_entry" | "member_bio" | "message";

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
  pinned_at: string | null;
}

export interface UnpinRevisionResponse {
  revision: ContentRevision | null;
  pending_action_id: string | null;
  finalize_after: string | null;
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

// ---- notifications -------------------------------------------------------

export type DestinationType = "web_push" | "webhook" | "ntfy" | "pushover";
export type DestinationState =
  | "pending_registration"
  | "active"
  | "disabled"
  | "pending_verification"
  | "declined_or_expired";
export type PayloadSensitivity = "full" | "minimal" | "bare";
export type CofrontRedaction = "count" | "someone" | "suppress";
export type RuleAction = "include" | "exclude";
export type IncludePrivate = "inherit" | "yes" | "no";

export interface WatchToken {
  id: string;
  system_id: string;
  label: string | null;
  revoked_at: string | null;
  created_at: string;
  updated_at: string;
  channel_count: number;
}

export interface WatchTokenCreate {
  label?: string | null;
}

export interface WatchTokenUpdate {
  label?: string | null;
}

export interface QuietHours {
  start: string;
  end: string;
  tz?: string;
}

export interface GroupRuleSpec {
  group_id: string;
  rule: RuleAction;
  include_private?: IncludePrivate;
}

export interface MemberRuleSpec {
  member_id: string;
  rule: RuleAction;
}

export interface NotificationChannel {
  id: string;
  watch_token_id: string;
  name: string;
  destination_type: DestinationType;
  destination_state: DestinationState;
  destination_config: Record<string, unknown>;
  event_type: string;
  activation_code_expires_at: string | null;
  redeemed_at: string | null;
  redeemed_by_account_id: string | null;
  base_all_members: boolean;
  base_include_private: boolean;
  trigger_on_start: boolean;
  trigger_on_stop: boolean;
  trigger_on_cofront_change: boolean;
  cofront_redaction: CofrontRedaction;
  payload_sensitivity: PayloadSensitivity;
  debounce_seconds: number;
  aggregation_window_seconds: number;
  quiet_hours: QuietHours | null;
  group_rules: GroupRuleSpec[];
  member_rules: MemberRuleSpec[];
  last_delivered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChannelCreate {
  name: string;
  destination_type: DestinationType;
  destination_config?: Record<string, unknown>;
  webhook_secret?: string | null;
  base_all_members?: boolean;
  base_include_private?: boolean;
  trigger_on_start?: boolean;
  trigger_on_stop?: boolean;
  trigger_on_cofront_change?: boolean;
  cofront_redaction?: CofrontRedaction;
  payload_sensitivity?: PayloadSensitivity;
  debounce_seconds?: number;
  aggregation_window_seconds?: number;
  quiet_hours?: QuietHours | null;
  group_rules?: GroupRuleSpec[];
  member_rules?: MemberRuleSpec[];
}

export interface ChannelUpdate {
  name?: string;
  destination_config?: Record<string, unknown>;
  webhook_secret?: string | null;
  base_all_members?: boolean;
  base_include_private?: boolean;
  trigger_on_start?: boolean;
  trigger_on_stop?: boolean;
  trigger_on_cofront_change?: boolean;
  cofront_redaction?: CofrontRedaction;
  payload_sensitivity?: PayloadSensitivity;
  debounce_seconds?: number;
  aggregation_window_seconds?: number;
  quiet_hours?: QuietHours | null;
  group_rules?: GroupRuleSpec[];
  member_rules?: MemberRuleSpec[];
}

export interface ChannelCreateResponse {
  channel: NotificationChannel;
  activation_url: string | null;
  activation_expires_at: string | null;
}

export interface ReissueActivationResponse {
  activation_url: string;
  activation_expires_at: string;
}

export interface PreviewMember {
  member_id: string;
  name: string;
  is_private: boolean;
  attribution: string;
}

export interface PreviewResponse {
  included: PreviewMember[];
  excluded: PreviewMember[];
  warnings: string[];
}

export interface TestDispatchResponse {
  delivered: boolean;
  error: string | null;
}

export interface ManageChannelView {
  channel_id: string;
  channel_name: string;
  system_label: string | null;
  destination_type: string;
  destination_state: string;
}

export interface ReceivingChannelView {
  channel_id: string;
  channel_name: string;
  system_label: string | null;
  destination_type: string;
  destination_state: string;
  redeemed_at: string | null;
  last_delivered_at: string | null;
}

export interface RedeemRequest {
  activation_code: string;
  push_subscription?: {
    endpoint: string;
    keys: Record<string, string>;
  };
}

export interface RedeemResponse {
  management_url: string;
  channel_name: string;
  system_label: string | null;
}

// --- Messages --------------------------------------------------------------

export type BoardKind = "system" | "member";

export interface Message {
  id: string;
  system_id: string;
  board_kind: BoardKind;
  board_member_id: string | null;
  author_member_id: string | null;
  author_member_name: string | null;
  parent_message_id: string | null;
  parent_preview: string | null;
  parent_author_member_name: string | null;
  body: string;
  created_at: string;
  updated_at: string;
}

export interface MessageCreate {
  board_kind: BoardKind;
  board_member_id?: string | null;
  author_member_id: string;
  parent_message_id?: string | null;
  body: string;
}

export interface MessageUpdate {
  body: string;
}

export interface MessagesPage {
  board_kind: BoardKind;
  board_member_id: string | null;
  messages: Message[];
  caller_last_seen_at: string | null;
}

export interface BoardSummary {
  board_kind: BoardKind;
  board_member_id: string | null;
  member_name: string | null;
  last_message_at: string | null;
  last_message_preview: string | null;
  message_count: number;
  unread_count: number;
}

export interface UnreadCounts {
  member_id: string;
  total: number;
  by_board: BoardSummary[];
}

export interface NotifyOnFrontSettings {
  notify_on_front_global: boolean;
  notify_on_front_self: boolean;
  notify_on_front_member_ids: string[];
}

export interface FrontStartPrompt {
  member_id: string;
  summaries: BoardSummary[];
  total_unread: number;
}

// --- Polls -----------------------------------------------------------------

export type PollKind = "single_choice" | "multi_choice";
export type PollResultsVisibility = "live" | "end_only";
export type PollVoteAction = "cast" | "change" | "withdraw";

export interface PollOption {
  id: string;
  text: string;
  position: number;
}

export interface PollTallyEntry {
  option_id: string;
  count: number;
}

export interface PollVote {
  voted_as_member_id: string;
  option_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface Poll {
  id: string;
  system_id: string;
  question: string;
  description: string | null;
  kind: PollKind;
  results_visibility: PollResultsVisibility;
  closes_at: string;
  retention_days: number;
  include_custom_fronts: boolean;
  options: PollOption[];
  is_closed: boolean;
  closed_since: string | null;
  purges_at: string;
  total_votes: number;
  tally: PollTallyEntry[] | null;
  votes: PollVote[] | null;
  created_at: string;
  updated_at: string;
}

export interface PollOptionCreate {
  text: string;
}

export interface PollCreate {
  question: string;
  description?: string | null;
  kind: PollKind;
  results_visibility: PollResultsVisibility;
  closes_at: string;
  retention_days?: number | null;
  include_custom_fronts?: boolean;
  options: PollOptionCreate[];
}

export interface VoteCast {
  voted_as_member_id: string;
  option_ids: string[];
}

export interface PollVoteEvent {
  id: string;
  voted_as_member_id: string | null;
  action: PollVoteAction;
  option_ids: string[];
  fronting_member_ids: string[];
  actor_user_id: string | null;
  created_at: string;
}

export interface PollAudit {
  poll_id: string;
  is_visible: boolean;
  events: PollVoteEvent[];
}

export interface PollServerConfig {
  tier: string;
  min_close_seconds: number;
  // 0 means "no upper bound"
  max_close_seconds: number;
  default_retention_days: number;
  // 0 means unlimited
  max_retention_days: number;
  // 0 means unlimited
  max_concurrent_open_polls: number;
}
