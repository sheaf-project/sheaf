"""Which notification transports this deployment can actually use.

One definition, because two surfaces ask the question and a disagreement
between them is a specific, nasty bug: the picker offers a channel type the
create endpoint then refuses, which is exactly the experience this module
exists to remove.

Only mobile push lives here. The other transports need no instance-side
credentials at all - a webhook, an ntfy topic and a Pushover key are supplied
per channel by the person creating it, and web push signs with a VAPID pair
the server generates for itself - so there is nothing to be unavailable.
"""

from __future__ import annotations

from sheaf.config import Settings

# Why a self-hoster cannot simply set the credentials and carry on. Kept as a
# constant because it is said in three places (the API's refusal, the web
# picker, the self-hosting guide) and all three have to say the same thing:
# the blocker is structural, not a missing config line, and someone who reads
# "not configured" alone will reasonably go looking for the setting.
MOBILE_PUSH_UNAVAILABLE_REASON = (
    "Mobile push is not available on this instance. A push credential is "
    "paired to an app build rather than to a server: an APNs key belongs to "
    "one Apple Developer team and must address the published app's bundle "
    "id, and an FCM token is minted against the Firebase project baked into "
    "the Android build. Sending to the App Store / Play Store builds of the "
    "Sheaf apps from another server is therefore not possible, and this "
    "instance has not been configured with credentials for app builds of "
    "its own. ntfy and Pushover reach a phone with no app builds to "
    "maintain, and both work here."
)


def fcm_configured(settings: Settings) -> bool:
    """FCM (Android) credentials present."""
    return bool(
        settings.fcm_service_account_path or settings.fcm_service_account_json
    )


def apns_configured(settings: Settings) -> bool:
    """APNs (iOS) credentials present.

    All four parts are required: a key on its own cannot be used without the
    team, key id and topic that say who it is and what it is addressing.
    """
    has_key = bool(settings.apns_p8_path or settings.apns_p8_key)
    return bool(
        settings.apns_team_id
        and settings.apns_key_id
        and settings.apns_bundle_id
        and has_key
    )


def mobile_push_available(settings: Settings) -> bool:
    """Can this instance dispatch mobile push to at least one platform?

    Either provider alone is enough. A single-platform deployment still wants
    to accept `mobile_push` channels and simply reach nothing on the platform
    it has no credentials for, rather than refusing the channel type outright.
    """
    return fcm_configured(settings) or apns_configured(settings)
