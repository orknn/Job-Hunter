"""
send_email.py — Gmail SMTP ile hazırlanan digest emailini gönderir.
Gmail App Password kullanır (normal şifre DEĞİL).
"""

import os
import sys
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
GMAIL_USERNAME = os.environ.get("GMAIL_USERNAME", "bicenorkun@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
RECIPIENT = os.environ.get("RECIPIENT_EMAIL", "bicenorkun@gmail.com")

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587


def send_email():
    """Send the job digest email via Gmail SMTP."""
    if not GMAIL_APP_PASSWORD:
        print("ERROR: GMAIL_APP_PASSWORD environment variable required.")
        print("Generate one at: https://myaccount.google.com/apppasswords")
        sys.exit(1)

    # Load the generated HTML
    html_path = os.path.join(os.path.dirname(__file__), "..", "data", "email_digest.html")
    if not os.path.exists(html_path):
        print("ERROR: No email_digest.html found. Run generate_email.py first.")
        sys.exit(1)

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Build email
    today = datetime.now().strftime("%d %b %Y")
    subject = f"🎯 Weekly Job Digest — {today}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Job Hunter <{GMAIL_USERNAME}>"
    msg["To"] = RECIPIENT

    # Plain text fallback
    text_part = MIMEText(
        f"Weekly Job Digest — {today}\n\n"
        "Your weekly job digest is ready. View this email in an HTML-capable client for the full experience.\n\n"
        "— Job Hunter Bot",
        "plain",
    )

    # HTML part
    html_part = MIMEText(html_content, "html")

    msg.attach(text_part)
    msg.attach(html_part)

    # Send
    print(f"📧 Sending digest to {RECIPIENT}...")
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(GMAIL_USERNAME, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USERNAME, RECIPIENT, msg.as_string())

        print(f"✅ Email sent successfully to {RECIPIENT}")
        print(f"   Subject: {subject}")

    except smtplib.SMTPAuthenticationError:
        print("❌ Gmail authentication failed!")
        print("   Make sure you're using an App Password, not your regular password.")
        print("   Generate one at: https://myaccount.google.com/apppasswords")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        sys.exit(1)


if __name__ == "__main__":
    send_email()
