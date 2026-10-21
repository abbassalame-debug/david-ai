"""Google Calendar + Gmail client for David."""
from david_config import DAVID_DATA
import datetime
import json
import os

CREDS_FILE = str(DAVID_DATA / "google_credentials.json")
TOKEN_FILE  = str(DAVID_DATA / "google_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.readonly",
]
API_TIMEOUT = 15


def _get_creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def _authed_http(creds):
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp
    return AuthorizedHttp(creds, http=httplib2.Http(timeout=API_TIMEOUT))


def get_today_events(max_results: int = 20) -> list[dict]:
    """Return today's calendar events as a list of dicts."""
    from googleapiclient.discovery import build

    creds = _get_creds()
    service = build("calendar", "v3", http=_authed_http(creds), cache_discovery=False)

    tz_offset = datetime.timezone(datetime.timedelta(hours=3))  # Demo User is GMT+3
    now = datetime.datetime.now(tz_offset)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end   = now.replace(hour=23, minute=59, second=59, microsecond=0)

    result = service.events().list(
        calendarId="primary",
        timeMin=day_start.isoformat(),
        timeMax=day_end.isoformat(),
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = []
    for e in result.get("items", []):
        start = e.get("start", {})
        end   = e.get("end", {})
        events.append({
            "summary":  e.get("summary", "(No title)"),
            "start":    start.get("dateTime", start.get("date", "")),
            "end":      end.get("dateTime", end.get("date", "")),
            "location": e.get("location", ""),
            "description": (e.get("description", "") or "")[:200],
        })
    return events


def get_unread_emails(max_results: int = 10) -> list[dict]:
    """Return recent unread Gmail messages."""
    from googleapiclient.discovery import build
    import base64

    creds = _get_creds()
    service = build("gmail", "v1", http=_authed_http(creds), cache_discovery=False)

    msgs = service.users().messages().list(
        userId="me", q="is:unread", maxResults=max_results
    ).execute().get("messages", [])

    emails = []
    for m in msgs:
        detail = service.users().messages().get(
            userId="me", id=m["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        snippet = detail.get("snippet", "")
        emails.append({
            "from":    headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "date":    headers.get("Date", ""),
            "snippet": snippet[:200],
        })
    return emails


def format_events_for_david(events: list[dict]) -> str:
    if not events:
        return "No events found for today."
    lines = []
    for e in events:
        start = e["start"]
        if "T" in start:
            try:
                dt = datetime.datetime.fromisoformat(start)
                start = dt.strftime("%-I:%M %p") if os.name != "nt" else dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                pass
        line = f"- {start}: {e['summary']}"
        if e.get("location"):
            line += f" @ {e['location']}"
        lines.append(line)
    return "\n".join(lines)


def format_emails_for_david(emails: list[dict]) -> str:
    if not emails:
        return "No unread emails."
    lines = []
    for e in emails:
        lines.append(f"- From: {e['from']} | Subject: {e['subject']} | {e['snippet'][:100]}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("=== TODAY'S EVENTS ===")
    events = get_today_events()
    print(format_events_for_david(events))
    print("\n=== UNREAD EMAILS ===")
    emails = get_unread_emails(5)
    print(format_emails_for_david(emails))
