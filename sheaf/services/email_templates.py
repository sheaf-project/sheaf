"""Transactional email templates.

Each function returns (subject, body_html, body_text).
Plain f-strings — no template engine dependency needed for ~5 templates.
"""

from sheaf.config import settings


def _base_url() -> str:
    return settings.sheaf_base_url.rstrip("/")


def verification_email(token: str) -> tuple[str, str, str]:
    link = f"{_base_url()}/verify-email?token={token}"
    subject = "Verify your Sheaf account"
    text = (
        f"Welcome to Sheaf!\n\n"
        f"Click the link below to verify your email address:\n\n"
        f"{link}\n\n"
        f"This link expires in 24 hours.\n\n"
        f"If you didn't create a Sheaf account, you can ignore this email."
    )
    html = (
        f"<h2>Welcome to Sheaf!</h2>"
        f"<p>Click the link below to verify your email address:</p>"
        f'<p><a href="{link}">Verify email</a></p>'
        f"<p>This link expires in 24 hours.</p>"
        f"<p>If you didn't create a Sheaf account, you can ignore this email.</p>"
    )
    return subject, html, text


def password_reset_email(token: str) -> tuple[str, str, str]:
    link = f"{_base_url()}/reset-password?token={token}"
    subject = "Reset your Sheaf password"
    text = (
        f"Someone requested a password reset for your Sheaf account.\n\n"
        f"Click the link below to set a new password:\n\n"
        f"{link}\n\n"
        f"This link expires in 1 hour.\n\n"
        f"If you didn't request this, you can ignore this email. "
        f"Your password won't be changed."
    )
    html = (
        f"<h2>Password reset</h2>"
        f"<p>Someone requested a password reset for your Sheaf account.</p>"
        f'<p><a href="{link}">Reset password</a></p>'
        f"<p>This link expires in 1 hour.</p>"
        f"<p>If you didn't request this, you can ignore this email. "
        f"Your password won't be changed.</p>"
    )
    return subject, html, text


def account_approved_email() -> tuple[str, str, str]:
    link = f"{_base_url()}/login"
    subject = "Your Sheaf account has been approved"
    text = (
        f"Your Sheaf account has been approved!\n\n"
        f"You can now log in at:\n\n"
        f"{link}"
    )
    html = (
        f"<h2>Account approved</h2>"
        f"<p>Your Sheaf account has been approved!</p>"
        f'<p><a href="{link}">Log in to Sheaf</a></p>'
    )
    return subject, html, text


def account_rejected_email() -> tuple[str, str, str]:
    subject = "Your Sheaf account registration"
    text = (
        "Your Sheaf account registration was not approved.\n\n"
        "If you believe this was a mistake, please contact the site administrator."
    )
    html = (
        "<h2>Registration not approved</h2>"
        "<p>Your Sheaf account registration was not approved.</p>"
        "<p>If you believe this was a mistake, please contact the site administrator.</p>"
    )
    return subject, html, text


def deletion_confirmation_email(cancel_by_date: str) -> tuple[str, str, str]:
    link = f"{_base_url()}/login"
    subject = "Your Sheaf account is scheduled for deletion"
    text = (
        f"Your Sheaf account has been scheduled for deletion.\n\n"
        f"Your account and all data will be permanently deleted after {cancel_by_date}.\n\n"
        f"To cancel, log in before then:\n\n"
        f"{link}\n\n"
        f"If you requested this, no action is needed."
    )
    html = (
        f"<h2>Account deletion scheduled</h2>"
        f"<p>Your Sheaf account has been scheduled for deletion.</p>"
        f"<p>Your account and all data will be permanently deleted after "
        f"<strong>{cancel_by_date}</strong>.</p>"
        f'<p>To cancel, <a href="{link}">log in</a> before then.</p>'
        f"<p>If you requested this, no action is needed.</p>"
    )
    return subject, html, text
