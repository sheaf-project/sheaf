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
  /** When the operator engages cf-shield, sessions for users with this
   *  flag set are invalidated so their traffic does not unwittingly
   *  traverse the Cloudflare CDN. Persisted regardless of whether the
   *  instance has shield_mode_enabled - the UI hides the toggle when
   *  the feature is dormant. */
  disable_cdn_during_ddos: boolean;
  uploads_allowed: boolean;
  bio_uploads_allowed: boolean;
  external_images_allowed: boolean;
  /** Whether this user may upload animated avatars (GIF / animated WebP).
   *  When false the cropper always flattens animated input to a still. */
  animated_uploads_allowed: boolean;
  /** Instance policy: whether this deployment serves a public-profile /
   *  share-link surface at all. When false the sharing UI is hidden and the
   *  public router 404s wholesale. */
  public_profiles_enabled: boolean;
  /** When the account self-declared 18+, or null if it hasn't. Creating a
   *  share grant is gated on this; the UI prompts for the declaration the
   *  first time the user tries to publish. */
  adult_attested_at: string | null;
}

/** Public payload from GET /v1/shield-mode/status. `feature_enabled`
 *  drives whether the Privacy/Security toggle is rendered at all. */
export interface ShieldModeStatus {
  feature_enabled: boolean;
  active: boolean;
  since: string | null;
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
  /** A raise of the master switch to public still waiting out the System
   *  Safety grace window. `privacy` above is still the live truth; this is
   *  what it becomes when `privacy_activates_at` passes. null = nothing
   *  staged. */
  pending_privacy: PrivacyLevel | null;
  privacy_activates_at: string | null;
  /** Operator takedown latch. When true, an admin has disabled publishing on
   *  this system: no new grant can be created and the system cannot be raised
   *  to public until an admin clears it. Read-only to the owner; the sharing
   *  screen surfaces it as a prominent banner. */
  publishing_blocked: boolean;
  delete_confirmation: DeleteConfirmation;
  date_format: DateFormat;
  /** Account display timezone. null = "automatic" (each device renders in
   *  its own local clock); otherwise an IANA zone name. This is the synced
   *  account default; a per-device override may shadow it locally. */
  timezone: string | null;
  replace_fronts_default: boolean;
  coalesce_contiguous_fronts: boolean;
  show_member_created_date: boolean;
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
  /** Omit to leave unchanged; null sets "automatic"; a string must be a
   *  valid IANA zone (the backend 422s an unknown zone). */
  timezone?: string | null;
  replace_fronts_default?: boolean;
  coalesce_contiguous_fronts?: boolean;
  show_member_created_date?: boolean;
  /** Step-up credentials, only consulted when raising privacy to public is a
   *  deferred exposure (the profile-visibility safety category is armed and a
   *  grant would actually serve). Never stored. */
  password?: string;
  totp_code?: string;
}

export interface Member {
  id: string;
  system_id: string;
  name: string;
  display_name: string | null;
  description: string | null;
  pronouns: string | null;
  avatar_url: string | null;
  banner_url: string | null;
  color: string | null;
  birthday: string | null;
  pluralkit_id: string | null;
  emoji: string | null;
  is_custom_front: boolean;
  privacy: PrivacyLevel;
  /** Lightweight scratchpad note; deliberately overwrite-only and not
   *  protected by System Safety. ~5kb plaintext cap. */
  note: string | null;
  /** Quick-switch pin priority. null = unpinned. A number pins the member
   *  to the top of the top-fronters list ahead of the recency ranking,
   *  ordered ascending. */
  quick_switch_pin: number | null;
  /** Hard share guard: this member appears in NO share view, ever. Setting it
   *  also removes them from every view immediately. */
  never_shareable: boolean;
  /** Hard share guard: this member may be in a view, but their front state
   *  never propagates to any public fronting projection. */
  fronting_private: boolean;
  /** A requested release of the fronting guard is waiting out the System
   *  Safety grace period until this timestamp. */
  fronting_private_activates_at: string | null;
  created_at: string;
  updated_at: string;
  /** True iff at least one ContentRevision exists for this member's
   *  bio. Populated by the /v1/members list + get endpoints; nested
   *  contexts (tag / group member lists) may return false even when
   *  history exists — open the bio history modal from the members
   *  route for an accurate signal. */
  has_bio_revisions: boolean;
  /** finalize_after timestamp if this member is in System Safety's
   *  pending-delete grace queue; null otherwise. Drives the "Pending
   *  delete" badge + dim styling in list views. */
  pending_delete_at: string | null;
  /** ISO timestamp when the member was archived (soft-hidden), or null
   *  when active. Archived members are excluded from pickers and the
   *  default member list view but still resolve in historical surfaces
   *  (front history, journals). */
  archived_at: string | null;
}

export interface MemberCreate {
  name: string;
  display_name?: string | null;
  description?: string | null;
  pronouns?: string | null;
  avatar_url?: string | null;
  banner_url?: string | null;
  color?: string | null;
  birthday?: string | null;
  pluralkit_id?: string | null;
  emoji?: string | null;
  is_custom_front?: boolean;
  privacy?: PrivacyLevel;
  note?: string | null;
  quick_switch_pin?: number | null;
  never_shareable?: boolean;
  fronting_private?: boolean;
}

export interface MemberUpdate {
  name?: string;
  display_name?: string | null;
  description?: string | null;
  pronouns?: string | null;
  avatar_url?: string | null;
  banner_url?: string | null;
  color?: string | null;
  birthday?: string | null;
  pluralkit_id?: string | null;
  emoji?: string | null;
  is_custom_front?: boolean;
  privacy?: PrivacyLevel;
  note?: string | null;
  /** Set a number to pin, or null to clear the pin. */
  quick_switch_pin?: number | null;
  never_shareable?: boolean;
  fronting_private?: boolean;
  password?: string;
  totp_code?: string;
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
  // True iff at least one audit row exists for this front. Lets the UI
  // grey out the history button on entries that have never been edited.
  has_audit_history: boolean;
  /** finalize_after timestamp if this front is in System Safety's
   *  pending-delete grace queue; null otherwise. Drives the "Pending
   *  delete" badge + dim styling in list views. */
  pending_delete_at: string | null;
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
  /** Pending-delete grace timestamp; null when not queued. */
  pending_delete_at: string | null;
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
  // All fields use presence-in-body to distinguish "omit" (keep) from
  // "explicitly set". Sending null on ended_at reopens a closed front;
  // sending null on custom_status clears it. started_at cannot be null.
  started_at?: string;
  ended_at?: string | null;
  member_ids?: string[];
  custom_status?: string | null;
}

export interface FrontSnapshot {
  started_at: string;
  ended_at: string | null;
  member_ids: string[];
  custom_status: string | null;
}

export interface FrontAuditEvent {
  id: string;
  front_id: string;
  actor_user_id: string | null;
  fronting_member_ids: string[];
  before: FrontSnapshot;
  after: FrontSnapshot;
  created_at: string;
}

export interface Group {
  id: string;
  system_id: string;
  name: string;
  description: string | null;
  color: string | null;
  parent_id: string | null;
  /** The same `PrivacyLevel` vocabulary a member carries, and the same
   *  meaning: a ceiling, not a promise. A group still only appears where a
   *  view was told to show groups. Private unless said otherwise. */
  privacy: PrivacyLevel;
  /** A raise still waiting out the safety grace window. `privacy` above is
   *  the truth right now; these say what it becomes and when. Both null when
   *  nothing is staged. */
  pending_privacy: PrivacyLevel | null;
  privacy_activates_at: string | null;
  created_at: string;
  updated_at: string;
  /** Pending-delete grace timestamp; null when not queued. */
  pending_delete_at: string | null;
}

export interface GroupCreate {
  name: string;
  description?: string | null;
  color?: string | null;
  parent_id?: string | null;
  privacy?: PrivacyLevel;
  /** Step-up credentials, sent only on the retry after the server asks for
   *  them. Creating a group already public is the same exposure as raising an
   *  existing one, so it goes through the same door - otherwise "delete it and
   *  add it back public" would be the way around the slower one. */
  password?: string;
  totp_code?: string;
}

export interface GroupUpdate {
  name?: string;
  description?: string | null;
  color?: string | null;
  parent_id?: string | null;
  privacy?: PrivacyLevel;
  /** Step-up credentials, sent only on the retry after the server asks for
   *  them: a raise that would actually put this group in front of someone is
   *  refused until it is confirmed. */
  password?: string;
  totp_code?: string;
}

export interface Tag {
  id: string;
  system_id: string;
  name: string;
  color: string | null;
  created_at: string;
  updated_at: string;
  /** Pending-delete grace timestamp; null when not queued. */
  pending_delete_at: string | null;
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
  /** The same `PrivacyLevel` vocabulary a member or a group carries, and the
   *  same meaning: a ceiling, not a promise. The field still only appears on a
   *  view that was told to show it. It applies to this field on EVERY member -
   *  there is no per-member-per-field setting. Private unless said otherwise. */
  privacy: PrivacyLevel;
  /** A raise still waiting out the safety grace window. `privacy` above is
   *  the truth right now; these say what it becomes and when. Both null when
   *  nothing is staged. */
  pending_privacy: PrivacyLevel | null;
  privacy_activates_at: string | null;
  created_at: string;
  updated_at: string;
  /** Pending-delete grace timestamp; null when not queued. */
  pending_delete_at: string | null;
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
  /** Step-up credentials, sent only on the retry after the server asks for
   *  them: a raise that would actually put this field in front of someone is
   *  refused until it is confirmed. */
  password?: string;
  totp_code?: string;
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
  | "channel_delete"
  | "reminder_delete"
  | "poll_delete"
  | "message_delete"
  | "message_thread_delete";

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
  applies_to_archive: boolean;
  applies_to_profile_visibility: boolean;
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
  applies_to_archive?: boolean;
  applies_to_profile_visibility?: boolean;
  auto_pin_first_revision?: boolean;
  password?: string;
  totp_code?: string;
}

export interface PendingExposure {
  kind: string;
  activates_at: string;
}

export interface SystemSafetyResponse {
  settings: SystemSafetySettings;
  pending_actions: PendingAction[];
  pending_changes: SafetyChangeRequest[];
  pending_exposures: PendingExposure[];
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

// Accepts unknown so callers whose result is a wider union (e.g. file delete
// returns FileDeleted | DeleteQueued) can use it too, not just DeleteResult.
export function isDeleteQueued(r: unknown): r is DeleteQueued {
  return (
    !!r &&
    typeof (r as DeleteQueued).pending_action_id === "string"
  );
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
  /** Pending-delete grace timestamp; null when not queued. */
  pending_delete_at: string | null;
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
  // Age-out window for closed fronting history, in days. 0 = off = keep
  // forever. Enabling or shortening this schedules deletion of history
  // older than the window (after a fixed import grace), so those changes
  // route through the System Safety grace + re-auth path; turning it off
  // or lengthening applies immediately.
  front_retention_days: number;
}

export interface RetentionUpdate {
  max_revisions?: number | null;
  max_revision_days?: number | null;
  // 0 = off = keep fronting history forever.
  front_retention_days?: number;
  password?: string;
  totp_code?: string;
}

// ---- notifications -------------------------------------------------------

export type DestinationType =
  | "web_push"
  | "webhook"
  | "ntfy"
  | "pushover"
  | "mobile_push"
  // Legacy mobile types kept so the type covers any pre-migration row that
  // somehow survives. Channel creation no longer produces these.
  | "fcm"
  | "apns_dev"
  | "apns_prod";
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
  /** Pending-revoke grace timestamp from System Safety; null when not
   *  queued. Drives the "Pending delete" badge in the watchers list. */
  pending_delete_at: string | null;
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
  /** Pending-delete grace timestamp; null when not queued. */
  pending_delete_at: string | null;
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
  /** True when the channel was disabled by the owner pausing it (not the
   *  recipient unsubscribing). Lets the recipient UI render "Paused by
   *  sender" instead of "Unsubscribed". */
  paused_by_sender: boolean;
}

export interface ReceivingChannelView {
  channel_id: string;
  channel_name: string;
  system_label: string | null;
  destination_type: string;
  destination_state: string;
  /** See ManageChannelView.paused_by_sender. */
  paused_by_sender: boolean;
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

export interface RedeemPreview {
  destination_type: DestinationType;
  channel_name: string;
  system_label: string | null;
  expires_at: string | null;
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
  /** Pending-delete grace timestamp; null when not queued. Unioned
   *  across single-message and thread deletes. */
  pending_delete_at: string | null;
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
  /** When true, only members in the current front at vote time may
   *  cast or change a vote. Default false; matches the journals model
   *  where any member can author regardless of front state. */
  restrict_voting_to_fronters: boolean;
  options: PollOption[];
  is_closed: boolean;
  closed_since: string | null;
  purges_at: string;
  total_votes: number;
  tally: PollTallyEntry[] | null;
  votes: PollVote[] | null;
  created_at: string;
  updated_at: string;
  /** Pending-delete grace timestamp; null when not queued. */
  pending_delete_at: string | null;
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
  restrict_voting_to_fronters?: boolean;
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

// --- Relationships --------------------------------------------------------

/** How a relationship type reads from each endpoint. `symmetric`: one label
 *  both ways (partner). `directional`: forward + reverse labels (parent/child).
 *  `either`: both - an edge is directional unless marked mutual, when both ends
 *  read the forward label (protector). */
export type RelationshipSymmetry = "symmetric" | "directional" | "either";

export interface RelationshipType {
  id: string;
  system_id: string;
  name: string;
  symmetry: RelationshipSymmetry;
  forward_label: string;
  reverse_label: string | null;
  /** Nullable, unlike a member's colour: a type may simply have none, and an
   *  explicit null on update clears it back to that. */
  color: string | null;
  created_at: string;
  updated_at: string;
}

export interface RelationshipTypeCreate {
  name: string;
  symmetry: RelationshipSymmetry;
  forward_label: string;
  reverse_label?: string | null;
  color?: string | null;
}

export interface RelationshipTypeUpdate {
  name?: string;
  forward_label?: string;
  reverse_label?: string | null;
  color?: string | null;
}

export interface RelationshipEdgeCreate {
  source_id: string;
  target_id: string;
  relationship_type_id: string;
  mutual?: boolean;
  /** Deliberately the same `PrivacyLevel` a member carries: "who may see this"
   *  is one question, so it gets one vocabulary rather than a parallel set of
   *  words that can drift apart. Private unless said otherwise. */
  visibility?: PrivacyLevel;
  /** Step-up credentials, sent only on the retry after the server asks for
   *  them. Creating an edge straight to public is the same exposure as raising
   *  an existing one, and runs the same gate. */
  password?: string;
  totp_code?: string;
}

export interface RelationshipEdge {
  id: string;
  source_id: string;
  target_id: string;
  relationship_type_id: string;
  mutual: boolean;
  visibility: PrivacyLevel;
  /** A raise still waiting out the safety grace window. `visibility` above is
   *  the truth right now; these say what it becomes and when. Both null when
   *  nothing is staged - and always null on group edges, which never stage. */
  pending_visibility: PrivacyLevel | null;
  visibility_activates_at: string | null;
  created_at: string;
}

export interface RelationshipEdgeUpdate {
  visibility?: PrivacyLevel;
  /** Swap the endpoints, so each reads the other's label. Directional and
   *  either types only; a symmetric type answers 400. */
  flip?: boolean;
  /** `either` types only, normalised off elsewhere (as on create). */
  mutual?: boolean;
  password?: string;
  totp_code?: string;
}

/** Direction of an edge as read from one node's viewpoint. */
export type RelationshipDirection = "none" | "outgoing" | "incoming";

/** One edge as it reads from a single member/group's perspective. The API
 *  resolves the label + direction server-side, so the client just displays it. */
export interface RelationshipFromViewpoint {
  id: string;
  relationship_type_id: string;
  type_name: string;
  other_id: string;
  label: string;
  direction: RelationshipDirection;
  mutual: boolean;
  visibility: PrivacyLevel;
  /** As on RelationshipEdge: what the level becomes and when, while
   *  `visibility` stays the truth until then. Always null for group edges. */
  pending_visibility: PrivacyLevel | null;
  visibility_activates_at: string | null;
}

export interface RelationshipGraphNode {
  id: string;
  name: string;
  avatar_url: string | null;
  color: string | null;
}

export interface RelationshipGraphEdge {
  id: string;
  source_id: string;
  target_id: string;
  relationship_type_id: string;
  type_name: string;
  source_label: string;
  target_label: string;
  mutual: boolean;
  directed: boolean;
}

export interface RelationshipGraph {
  nodes: RelationshipGraphNode[];
  edges: RelationshipGraphEdge[];
}

/** Client-side preset templates for the type editor. Selecting one populates
 *  the new-type form; the user then tweaks and saves. These are NOT seeded on
 *  the server - a system's types start empty. */
export interface RelationshipPreset {
  label: string;
  name: string;
  symmetry: RelationshipSymmetry;
  forward_label: string;
  reverse_label: string | null;
  /** Suggested starting colour, editable (and clearable) like any other. */
  color?: string;
}

/** The preset colours are deliberately muted mid-tones: each one has enough
 *  contrast to read against both the light and the dark theme, and no two are
 *  told apart by red-vs-green alone, so the graph still parses for someone who
 *  can't separate those. They are a starting point, not a meaning - a colour
 *  never carries information the label doesn't already say. */
export const RELATIONSHIP_PRESETS: RelationshipPreset[] = [
  { label: "Partner", name: "Partner", symmetry: "symmetric", forward_label: "partner", reverse_label: null, color: "#c2708f" },
  { label: "Friend", name: "Friend", symmetry: "symmetric", forward_label: "friend", reverse_label: null, color: "#6a9fb5" },
  { label: "Sibling", name: "Sibling", symmetry: "symmetric", forward_label: "sibling", reverse_label: null, color: "#7f9c6c" },
  { label: "Parent / Child", name: "Parent", symmetry: "directional", forward_label: "parent", reverse_label: "child", color: "#b5894a" },
  { label: "Protector / Protectee", name: "Protector", symmetry: "either", forward_label: "protector", reverse_label: "protectee", color: "#7c6fa8" },
  { label: "Caretaker", name: "Caretaker", symmetry: "either", forward_label: "caretaker", reverse_label: "cared for", color: "#4f9a92" },
  { label: "Split from", name: "Split", symmetry: "directional", forward_label: "split from", reverse_label: "split off", color: "#9a7b6a" },
];

// --- Share views + grants (public profiles) ---

export type ShareSubjectType = "public" | "link";
export type ShareGrantStatus = "pending" | "active" | "revoked";
export type ShareItemStatus = "pending" | "active";

/** Why a member sitting in a view is not actually being served. Mirrors
 *  `share_projection.NOT_SERVED_REASONS`; the client renders these rather than
 *  re-deriving any of them, which is what it used to do off member privacy
 *  alone - quietly missing the archived and deletion-queued cases, both of
 *  which drop a member from the public page at once. */
export type ShareNotServedReason =
  | "never_shareable"
  | "deletion_queued"
  | "archived"
  | "private"
  | "pending";

export interface ShareViewMemberRow {
  id: string;
  member_id: string;
  status: ShareItemStatus;
  activates_at: string | null;
  /** Whether this member is ACTUALLY on the page right now, answered by the
   *  server through the projection's own filter. Optional so the UI still
   *  works against a server that predates the field. */
  served?: boolean;
  /** Why not, when `served` is false. Null while served, and also in the case
   *  where the server cannot name a reason - the row then reads as "won't
   *  show" with no explanation rather than an invented one. */
  not_served_reason?: ShareNotServedReason | null;
  /** The group expansion that put this member in the view, or null when they
   *  were picked by hand (also null once that group is deleted). This is the
   *  set detaching that group removes - never the group's current roster, which
   *  is a different set the moment anyone joins or leaves it. */
  added_via_group_id?: string | null;
}

export interface ShareViewFieldRow {
  id: string;
  field_id: string;
  status: ShareItemStatus;
  activates_at: string | null;
}

export interface ShareViewGroupRow {
  id: string;
  group_id: string;
  synced_at: string;
}

export interface ShareView {
  id: string;
  name: string;
  /** Whether the view serves the member roster at all. With it off nothing
   *  member-shaped is served, so the options that decorate a member (bios,
   *  relationships, permalinks) have nothing to attach to. */
  include_members: boolean;
  include_bio: boolean;
  include_fronting: boolean;
  fronting_show_count: boolean;
  include_relationships: boolean;
  /** Whether the view serves the system's public groups. */
  include_groups: boolean;
  /** Stable per-member URLs for members this view already shows. Deliberately
   *  NOT an exposure flag: it publishes no data that the roster doesn't
   *  already publish, only an address for it. So it has no pending twin,
   *  applies immediately in both directions, and never demands step-up. */
  member_permalinks: boolean;
  /** A loosening of one of the exposure flags above that is still waiting out
   *  the grace period: null (or absent) when nothing is queued for that flag.
   *  Optional so the UI works against a server that doesn't defer them. */
  pending_include_members?: boolean | null;
  pending_include_bio?: boolean | null;
  pending_include_fronting?: boolean | null;
  pending_fronting_show_count?: boolean | null;
  pending_include_relationships?: boolean | null;
  pending_include_groups?: boolean | null;
  /** When the queued flag change above becomes live. */
  flags_activate_at?: string | null;
  created_at: string;
  /** True when any non-revoked grant (live or still in its grace window)
   *  points at this view. */
  is_shared: boolean;
  members: ShareViewMemberRow[];
  fields: ShareViewFieldRow[];
  groups: ShareViewGroupRow[];
}

export interface ShareViewCreate {
  name: string;
  include_members?: boolean;
  include_bio?: boolean;
  include_fronting?: boolean;
  fronting_show_count?: boolean;
  include_relationships?: boolean;
  include_groups?: boolean;
  member_permalinks?: boolean;
}

export interface ShareViewUpdate {
  name?: string;
  include_members?: boolean;
  include_bio?: boolean;
  include_fronting?: boolean;
  fronting_show_count?: boolean;
  include_relationships?: boolean;
  include_groups?: boolean;
  member_permalinks?: boolean;
  password?: string;
  totp_code?: string;
}

export interface ShareViewGroupAddResult {
  added: number;
  skipped_never_shareable: number;
  /** Members whose privacy (private/friends) keeps them off the public tier,
   *  so the bulk group-add left them out. */
  skipped_not_public: number;
}

export interface ShareGrant {
  id: string;
  view_id: string;
  subject_type: ShareSubjectType;
  note: string | null;
  status: ShareGrantStatus;
  activates_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

export interface ShareGrantCreate {
  view_id: string;
  subject_type: ShareSubjectType;
  note?: string | null;
  expires_at?: string | null;
  password?: string;
  totp_code?: string;
}

/** A newly created or rotated grant. `token` is the raw link token, present
 *  ONLY for link grants and ONLY on the response that created/rotated it. */
export interface ShareGrantCreated {
  grant: ShareGrant;
  token: string | null;
}

export interface ShareAuditEntry {
  grant: ShareGrant;
  view_id: string;
  view_name: string;
  /** The CURATED count: how many members the owner put in this view. Describes
   *  their curation, not what a visitor gets. */
  member_count: number;
  /** The SERVED count: how many of those the projection would actually show
   *  right now. Null when the roster is off entirely, matching
   *  `PublicSystemView.member_count` - a roster the view refuses to serve must
   *  not be countable, and zero would be a claim. Optional so the UI still
   *  works against a server that predates the field; where it differs from
   *  `member_count`, the audit line says "3 of 5". */
  served_member_count?: number | null;
  field_count: number;
  /** False when the roster is off entirely - the counts above then describe
   *  curation that is not being published, so an audit line has to say so
   *  rather than quoting a number on its own. */
  include_members: boolean;
  include_bio: boolean;
  include_fronting: boolean;
  include_relationships: boolean;
  include_groups: boolean;
  member_permalinks: boolean;
  /** Edges this view would actually serve right now, not what the flag
   *  permits: zero when the flag is off, and zero when it is on but no edge
   *  clears both its own `public` level and the member ceiling. */
  relationship_count: number;
  /** Groups this view would actually serve right now, on the same "what is
   *  really served" basis as `relationship_count`: zero when the flag is off. */
  group_count: number;
}

export interface ShareAudit {
  entries: ShareAuditEntry[];
  /** Null while the profile is actually served. Otherwise the reason nothing
   *  below is reachable right now - `"publishing_blocked"` (an operator has
   *  latched the system shut, and only they can lift it), `"system_private"`
   *  (the system's own privacy gates every grant at once) or `"account_state"`.
   *  Reported in that order when more than one applies. Account-level rather
   *  than per-entry because it suppresses the lot: the grants and the counts
   *  above stay accurate as curation, but every public URL 404s. */
  profile_suppressed: string | null;
}

export interface AdultAttestation {
  adult_attested_at: string | null;
}

// --- Public projection payloads (anonymous /v1/public/... surface) ---

export interface PublicMemberView {
  id: string;
  /** The one name this surface has, and it is already the shown one: the
   *  display name where there is one, the member's own name otherwise.
   *  Publishing a canonical name alongside a display name would make the
   *  display name cosmetic, since anyone reading the JSON gets both. */
  name: string;
  pronouns: string | null;
  avatar_url: string | null;
  banner_url: string | null;
  color: string | null;
  bio: string | null;
  fields: Record<string, unknown>;
}

export interface PublicSystemView {
  /** The system's own id, and only on a public profile - the page whose URL
   *  already contains it. Null behind a share link: the link is an opaque
   *  token so the system it belongs to cannot be named from it, and an id in
   *  the body would have let two links, or a link and a public profile, be
   *  tied back to one system by anything reading the JSON. */
  id: string | null;
  name: string;
  description: string | null;
  avatar_url: string | null;
  color: string | null;
  tag: string | null;
  /** Null when the view does not serve its member roster. A roster the view
   *  refuses to show must not be countable either, and null says that where a
   *  zero would be a claim. */
  member_count: number | null;
  /** Whether this view gives each member an address of its own. Presentation
   *  configuration: the client uses it to decide whether a member card is a
   *  link or opens in place. */
  member_permalinks: boolean;
}

/** One member of a published group: id and name only, like a relationship
 *  endpoint. Everyone listed is already published in full through /members. */
export interface PublicGroupMember {
  id: string;
  name: string;
}

/** One group a view publishes. Its member list is an INTERSECTION with the
 *  members the view already shows, never a second allowlist, so a published
 *  group can never name somebody new. A public group whose intersection is
 *  EMPTY is not in the payload at all: a name with nobody behind it still
 *  tells a visitor such a group exists here, which is not something the owner
 *  published by publishing a roster. */
export interface PublicGroupView {
  id: string;
  name: string;
  description: string | null;
  color: string | null;
  members: PublicGroupMember[];
}

export interface PublicGroupsView {
  groups: PublicGroupView[];
}

export interface PublicFrontingMember {
  id: string;
  /** Already the shown name, on the same single-name basis as
   *  `PublicMemberView.name`. */
  name: string;
  pronouns: string | null;
  avatar_url: string | null;
  color: string | null;
  since: string | null;
}

export interface PublicFrontingView {
  members: PublicFrontingMember[];
  hidden_count: number;
}

/** One end of a published edge: id and name only. The endpoint is always a
 *  member the same view publishes in full through /members, so the client
 *  joins on `id` for anything richer. */
export interface PublicRelationshipEndpoint {
  id: string;
  name: string;
}

export interface PublicRelationship {
  id: string;
  type_name: string;
  type_color: string | null;
  source: PublicRelationshipEndpoint;
  target: PublicRelationshipEndpoint;
  /** How the edge reads from each end ("parent" / "child"). Both are the
   *  forward label for symmetric types and for mutual either-edges. */
  source_label: string;
  target_label: string;
  /** True only for an `either` edge the owner marked mutual. With the two
   *  labels, this decides whether an arrow is drawn at all. */
  mutual: boolean;
}

export interface PublicRelationshipsView {
  relationships: PublicRelationship[];
}

/**
 * One share view rendered exactly as a visitor would receive it, from
 * `GET /v1/share-views/{id}/preview`.
 *
 * Every section is the SAME payload type the anonymous surface serves, because
 * the server builds it with the same projection functions - which is what stops
 * the preview and the real page from drifting apart.
 *
 * `null` on a section is the bundle's spelling of that endpoint's 404: the view
 * does not serve it. Deliberately not an empty list, because empty is a real
 * state a served section can be in ("nobody is fronting") and the owner needs
 * to tell that apart from "visitors cannot see who is fronting".
 */
export interface SharePreview {
  system: PublicSystemView;
  members: PublicMemberView[] | null;
  fronting: PublicFrontingView | null;
  relationships: PublicRelationshipsView | null;
  groups: PublicGroupsView | null;
  /** Why none of this would reach anybody right now, or null. Same coarse
   *  values as `ShareAudit.profile_suppressed`. The sections stay populated
   *  when it is set - the preview answers "what would visitors see", and this
   *  answers "is anyone getting it", which are different questions. */
  suppressed: string | null;
}
