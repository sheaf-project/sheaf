"""Folding a crawler's User-Agent into a bounded unfurler label.

Host-only and pure. The contract is the same as the client-family one: a
stranger's header can be anything, and nothing they put in it may become a
Prometheus series.
"""

from __future__ import annotations

import pytest

from sheaf.observability.unfurler import UNFURLERS, unfurler_from


@pytest.mark.parametrize(
    ("user_agent", "expected"),
    [
        ("Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)", "discord"),
        ("Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)", "slack"),
        ("TelegramBot (like TwitterBot)", "telegram"),
        ("http.rb/5.1.1 (Mastodon/4.2.0; +https://example.social/)", "mastodon"),
        ("Synapse (bot; +https://github.com/matrix-org/synapse)", "matrix"),
        ("Mozilla/5.0 (compatible; Bluesky Cardyb/1.1; +mailto:support@bsky.app)", "bluesky"),
        ("Twitterbot/1.0", "twitter"),
        ("facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)", "facebook"),
        ("WhatsApp/2.23.20.0", "whatsapp"),
        # The proxy lets these through; they are real but not worth a series.
        ("LinkedInBot/1.0 (compatible; Mozilla/5.0; +http://www.linkedin.com)", "other"),
        ("Iframely/1.3.1 (+https://iframely.com/docs/about)", "other"),
        # And everything that is not an unfurler at all.
        ("Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0", "other"),
        ("curl/8.5.0", "other"),
        ("", "other"),
        (None, "other"),
    ],
)
def test_user_agent_folds_into_a_bounded_unfurler(user_agent: str | None, expected: str):
    got = unfurler_from(user_agent)
    assert got == expected
    assert got in UNFURLERS


def test_matching_is_case_insensitive():
    assert unfurler_from("DISCORDBOT/2.0") == "discord"
    assert unfurler_from("discordbot") == "discord"


def test_telegram_wins_over_the_twitter_it_claims_to_be_like():
    """Telegram's UA literally says "(like TwitterBot)". First marker in table
    order wins, and telegram is listed before twitter, so it is attributed to
    the service that actually fetched."""
    assert unfurler_from("TelegramBot (like TwitterBot)") == "telegram"
