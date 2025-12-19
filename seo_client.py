# seo_client.py
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd

SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _authorize(json_keyfile: str):
    creds = ServiceAccountCredentials.from_json_keyfile_name(json_keyfile, SCOPES)
    return gspread.authorize(creds)


def open_sheet_by_url(json_keyfile, sheet_url, gid=None, worksheet_title: str | None = None):
    client = _authorize(json_keyfile)
    sh = client.open_by_url(sheet_url)

    ws = None

    # Prefer explicit worksheet title if provided
    if worksheet_title:
        try:
            ws = sh.worksheet(worksheet_title)
        except Exception:
            ws = None

    # Otherwise select by gid if provided
    if ws is None and gid is not None:
        try:
            ws = next(w for w in sh.worksheets() if w.id == gid)
        except Exception:
            ws = None

    # Final fallback: first worksheet
    if ws is None:
        ws = sh.get_worksheet(0)

    data = ws.get_all_records()
    return pd.DataFrame(data)


def open_all_worksheets_by_url(json_keyfile: str, sheet_url: str) -> dict[str, pd.DataFrame]:
    """Return {worksheet_title: DataFrame} for all tabs in the Google Sheet."""
    client = _authorize(json_keyfile)
    sh = client.open_by_url(sheet_url)

    out: dict[str, pd.DataFrame] = {}
    for ws in sh.worksheets():
        try:
            out[ws.title] = pd.DataFrame(ws.get_all_records())
        except Exception:
            # Skip tabs that can't be read (permissions, empty, etc.)
            continue
    return out
