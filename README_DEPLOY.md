# Neitzel Lab Inventory (Streamlit)

A shareable chemical inventory app for the Neitzel Lab.

## Quick start on Streamlit Community Cloud

1. Create a GitHub repo and add:
   - `app.py`
   - `chemicals_master.csv`
   - `requirements.txt`
   - `.gitignore`

2. Deploy:
   - Go to https://share.streamlit.io
   - Connect GitHub → pick repo/branch → set main file to `app.py` → Deploy.

3. Share the URL with the lab.

---

## Keeping data in sync

By default the app writes to a local CSV on the server. That does **not** auto-commit back to GitHub.

### Option A: CSV via Git (manual)
- Add a **Download current CSV** button in the app (see snippet below).
- Periodically download and commit the CSV back to GitHub (replace the file in the repo). Streamlit redeploys automatically.

### Option B: Google Sheets backend (recommended)
- Use a shared Google Sheet so edits are live for everyone.
- Steps:
  1) Uncomment `gspread` and `google-auth` in `requirements.txt`.
  2) Create a Google Cloud service account, enable Sheets + Drive APIs, generate JSON key.
  3) Share the sheet with the service account email (Editor).
  4) In Streamlit Cloud → **Settings → Secrets**, paste TOML like:
     ```toml
     [gsheets]
     enabled = true
     spreadsheet_url = "https://docs.google.com/spreadsheets/d/...."
     type = "service_account"
     project_id = "..."
     private_key_id = "..."
     private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
     client_email = "....@....iam.gserviceaccount.com"
     client_id = "..."
     token_uri = "https://oauth2.googleapis.com/token"
     ```
  5) Replace `load_data`/`save_data` with the Google Sheets–aware versions below.

---

## Snippets

### Add a Download CSV button (Inventory tab)
```python
st.download_button(
    "⬇️ Download current CSV",
    data=load_data().to_csv(index=False).encode("utf-8"),
    file_name="chemicals_master.csv",
    mime="text/csv",
)
```

### Google Sheets–aware load/save (drop-in)
```python
import pandas as pd
import streamlit as st

EXPECTED_COLS = [
    "name","cas","carbons","distributor","container_size",
    "state","location","bottles","storage_conditions","hazards","sds_link"
]

def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in EXPECTED_COLS: 
        if c not in df.columns: df[c] = pd.NA
    for c in ["name","cas","distributor","container_size","state","location","storage_conditions","hazards","sds_link"]:
        df[c] = df[c].astype(str).replace({"nan":""}).fillna("")
    df["bottles"] = pd.to_numeric(df["bottles"], errors="coerce").fillna(1).astype(int)
    df["carbons"] = pd.to_numeric(df["carbons"], errors="coerce")
    return df[EXPECTED_COLS]

def _gsheets_enabled():
    try:
        return bool(st.secrets.get("gsheets", {}).get("enabled"))
    except Exception:
        return False

def load_data() -> pd.DataFrame:
    if _gsheets_enabled():
        import gspread
        from google.oauth2.service_account import Credentials
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gsheets"])
        spreadsheet_url = creds_dict.pop("spreadsheet_url")
        creds_dict.pop("enabled", None)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        gc = gspread.authorize(creds)
        ws = gc.open_by_url(spreadsheet_url).sheet1
        values = ws.get_all_values()
        if not values: return pd.DataFrame(columns=EXPECTED_COLS)
        headers, rows = values[0], values[1:]
        df = pd.DataFrame(rows, columns=headers)
        if "bottles" in df.columns: df["bottles"] = pd.to_numeric(df["bottles"], errors="coerce")
        if "carbons" in df.columns: df["carbons"] = pd.to_numeric(df["carbons"], errors="coerce")
        return _normalize_df(df)
    else:
        import os
        DATA_FILE = "chemicals_master.csv"
        if os.path.exists(DATA_FILE):
            try: return _normalize_df(pd.read_csv(DATA_FILE))
            except Exception: return pd.DataFrame(columns=EXPECTED_COLS)
        return pd.DataFrame(columns=EXPECTED_COLS)

def save_data(df: pd.DataFrame):
    df = _normalize_df(df)
    if _gsheets_enabled():
        import gspread
        from google.oauth2.service_account import Credentials
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds_dict = dict(st.secrets["gsheets"])
        spreadsheet_url = creds_dict.pop("spreadsheet_url")
        creds_dict.pop("enabled", None)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        gc = gspread.authorize(creds)
        ws = gc.open_by_url(spreadsheet_url).sheet1
        data = [EXPECTED_COLS] + df[EXPECTED_COLS].astype(str).values.tolist()
        ws.clear(); ws.update("A1", data)
    else:
        DATA_FILE = "chemicals_master.csv"
        df.to_csv(DATA_FILE, index=False)
```
