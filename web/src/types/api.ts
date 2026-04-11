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
  newsletter_opt_in: boolean;
  email_delivery_status: "ok" | "soft_bouncing" | "hard_bounced" | "complained";
  email_revalidation_required: boolean;
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
