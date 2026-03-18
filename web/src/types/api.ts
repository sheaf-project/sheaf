export type PrivacyLevel = "public" | "friends" | "private";

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
  created_at: string;
  last_login_at: string | null;
}

export interface System {
  id: string;
  name: string;
  description: string | null;
  tag: string | null;
  avatar_url: string | null;
  color: string | null;
  privacy: PrivacyLevel;
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
