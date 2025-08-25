import streamlit as st
import pandas as pd
import os
from io import BytesIO
import requests, re, uuid

st.set_page_config(page_title="Neitzel Lab Inventory", layout="wide", page_icon="🧪")

POLYMER_CSS = """
.stTabs [data-baseweb="tab-list"] { gap: 2.25rem; }
.stTabs [data-baseweb="tab"] { padding: 0.75rem 1.25rem; border-radius: 999px; }
html, body, [data-testid="stAppViewContainer"] {
  background-image: radial-gradient(rgba(0,0,0,0.03) 1px, transparent 1px),
                    radial-gradient(rgba(0,0,0,0.03) 1px, transparent 1px);
  background-size: 24px 24px, 48px 48px;
  background-position: 0 0, 12px 12px;
}
h1, h2, h3 { letter-spacing: 0.2px; }
[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
"""

st.markdown(f"<style>{POLYMER_CSS}</style>", unsafe_allow_html=True)

DATA_FILE = "chemicals_master.csv"
EXPECTED_COLS = [
    "name", "cas", "carbons", "distributor", "container_size",
    "state", "location", "bottles", "storage_conditions", "hazards", "sds_link"
]
# Unique row identifier used for precise deletes even when names are duplicated
ROW_ID = "_row_id"

def template_csv_bytes():
    buf = BytesIO()
    pd.DataFrame(columns=EXPECTED_COLS).to_csv(buf, index=False)
    buf.seek(0)
    return buf

def load_data() -> pd.DataFrame:
    if os.path.exists(DATA_FILE):
        try:
            df = pd.read_csv(DATA_FILE)
            # Ensure required columns
            for c in EXPECTED_COLS:
                if c not in df.columns:
                    df[c] = pd.NA
            # Create/repair unique row ids
            if ROW_ID not in df.columns:
                df[ROW_ID] = [str(uuid.uuid4()) for _ in range(len(df))]
            df[ROW_ID] = df[ROW_ID].astype(str)
            # Clean types
            df["bottles"] = pd.to_numeric(df["bottles"], errors="coerce").fillna(1).astype(int)
            for c in ["name","cas","distributor","container_size","state","location","storage_conditions","hazards","sds_link"]:
                df[c] = df[c].astype(str).replace({"nan":""}).fillna("")
            df["carbons"] = pd.to_numeric(df["carbons"], errors="coerce")
            # Return with ROW_ID so views can reference it
            cols = EXPECTED_COLS + ([ROW_ID] if ROW_ID in df.columns else [])
            return df[cols]
        except Exception:
            return pd.DataFrame(columns=EXPECTED_COLS + [ROW_ID])
    return pd.DataFrame(columns=EXPECTED_COLS + [ROW_ID])
    return pd.DataFrame(columns=EXPECTED_COLS)

def save_data(df: pd.DataFrame):
    df = df.copy()
    # Ensure schema
    for c in EXPECTED_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    if ROW_ID not in df.columns:
        df[ROW_ID] = [str(uuid.uuid4()) for _ in range(len(df))]
    df[ROW_ID] = df[ROW_ID].astype(str)
    # Normalize
    df["bottles"] = pd.to_numeric(df["bottles"], errors="coerce").fillna(1).astype(int)
    for c in ["name","cas","distributor","container_size","state","location","storage_conditions","hazards","sds_link"]:
        df[c] = df[c].astype(str).replace({"nan":""}).fillna("")
    # Persist with row ids so deletes are stable
    cols = EXPECTED_COLS + [ROW_ID]
    df[cols].to_csv(DATA_FILE, index=False)

# Enhanced external chemical info fetcher
def fetch_details(query: str):
    details = {"name": query, "cas": "", "carbons": "N/A", "formula": "", "hazards": "", "sds_link": f"https://www.google.com/search?q={query}+SDS"}
    try:
        # PubChem compound summary
        cid_r = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{query}/cids/JSON", timeout=10)
        if cid_r.status_code == 200 and "IdentifierList" in cid_r.json():
            cid = cid_r.json()["IdentifierList"]["CID"][0]
            summary_r = requests.get(f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON", timeout=10)
            if summary_r.status_code == 200:
                js = summary_r.json()
                # Formula
                for sec in js.get("Record", {}).get("Section", []):
                    if sec.get("TOCHeading") == "Names and Identifiers":
                        for s2 in sec.get("Section", []):
                            if s2.get("TOCHeading") == "Molecular Formula":
                                for it in s2.get("Information", []):
                                    details["formula"] = it.get("StringValue", "")
                                    if details["formula"]:
                                        m = re.search(r"C(\d+)", details["formula"])
                                        details["carbons"] = int(m.group(1)) if m else "N/A"
                    if sec.get("TOCHeading") == "CAS":
                        for it in sec.get("Information", []):
                            details["cas"] = it.get("StringValue", "")
                # Hazards
                for sec in js.get("Record", {}).get("Section", []):
                    if sec.get("TOCHeading") == "Safety and Hazards":
                        for s2 in sec.get("Section", []):
                            if s2.get("TOCHeading") == "GHS Classification":
                                hazards = []
                                for it in s2.get("Information", []):
                                    for itm in it.get("StringWithMarkup", []):
                                        hazards.append(itm.get("String", ""))
                                details["hazards"] = "\n".join(hazards)
                            if s2.get("TOCHeading") == "Safety Sources":
                                for it in s2.get("Information", []):
                                    for ref in it.get("Reference", []):
                                        if ref.get("URL"):
                                            details["sds_link"] = ref["URL"]
                                            break
    except Exception:
        pass
    return details

# =============================
# Google Sheets backup helpers (optional)
# =============================

def _gsheets_enabled() -> bool:
    """Return True if [gsheets] secrets are configured with enabled=true."""
    try:
        return bool(st.secrets.get("gsheets", {}).get("enabled"))
    except Exception:
        return False


def _open_gsheets():
    """Open the Google Sheet and return (worksheet, spreadsheet_url).
    Requires Streamlit secrets with a [gsheets] section.
    """
    import gspread  # imported only when needed
    from google.oauth2.service_account import Credentials

    cfg = dict(st.secrets["gsheets"])  # copy
    spreadsheet_url = cfg.pop("spreadsheet_url")
    cfg.pop("enabled", None)
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(cfg, scopes=scope)
    client = gspread.authorize(creds)
    sh = client.open_by_url(spreadsheet_url)
    ws = sh.sheet1
    return ws, spreadsheet_url


def _ensure_schema_for_backup(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in EXPECTED_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    for c in [
        "name","cas","distributor","container_size","state","location",
        "storage_conditions","hazards","sds_link",
    ]:
        df[c] = df[c].astype(str).replace({"nan": ""}).fillna("")
    df["bottles"] = pd.to_numeric(df["bottles"], errors="coerce").fillna(1).astype(int)
    df["carbons"] = pd.to_numeric(df["carbons"], errors="coerce")
    if ROW_ID not in df.columns:
        df[ROW_ID] = [str(uuid.uuid4()) for _ in range(len(df))]
    df[ROW_ID] = df[ROW_ID].astype(str)
    return df[EXPECTED_COLS + [ROW_ID]]


def _backup_to_gsheets(df: pd.DataFrame):
    """Write the current inventory to Google Sheets (overwrites sheet1)."""
    ws, _ = _open_gsheets()
    df = _ensure_schema_for_backup(df)
    data = [EXPECTED_COLS] + df[EXPECTED_COLS].astype(str).values.tolist()
    ws.clear()
    ws.update("A1", data)


def _restore_from_gsheets() -> pd.DataFrame:
    """Read inventory from Google Sheets sheet1 and return a normalized DataFrame."""
    ws, _ = _open_gsheets()
    values = ws.get_all_values()
    if not values:
        return pd.DataFrame(columns=EXPECTED_COLS + [ROW_ID])
    headers = values[0]
    rows = values[1:]
    df = pd.DataFrame(rows, columns=headers)
    # normalize
    for c in EXPECTED_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    if "bottles" in df.columns:
        df["bottles"] = pd.to_numeric(df["bottles"], errors="coerce").fillna(1).astype(int)
    if "carbons" in df.columns:
        df["carbons"] = pd.to_numeric(df["carbons"], errors="coerce")
    if ROW_ID not in df.columns:
        df[ROW_ID] = [str(uuid.uuid4()) for _ in range(len(df))]
    df[ROW_ID] = df[ROW_ID].astype(str)
    return df[EXPECTED_COLS + [ROW_ID]]

# =============================
# UI Tabs
# =============================

t1, t2, t3, t4 = st.tabs(["Inventory", "Add Chemicals", "Upload / Merge", "Settings"])

# ---------- Inventory ----------
with t1:
    st.title("🔬 Neitzel Lab Inventory")
    df = load_data()

    search_q = st.text_input("Search (name/CAS/hazards)")

    if not df.empty:
        locations = sorted([x for x in df["location"].dropna().unique().tolist() if str(x).strip()])
        tabs = st.tabs(["All"] + locations)

        def render_view(view_df, loc_label):
            key_suffix = re.sub(r'[^A-Za-z0-9_]+','_', str(loc_label))
            view = view_df.copy()
            if search_q:
                ql = search_q.lower()
                view = view[view.apply(lambda r: any(ql in str(r[c]).lower() for c in ["name","cas","hazards","location","distributor"]), axis=1)]
            display = view.copy()
            display["carbons"] = display["carbons"].apply(lambda x: int(x) if pd.notna(x) else "N/A")
            display["state"] = display["state"].apply(lambda x: x if str(x).strip() else "N/A")
            st.data_editor(
                display,
                use_container_width=True,
                disabled=True,
                column_config={
                    "sds_link": st.column_config.LinkColumn("SDS", display_text="SDS"),
                },
                key=f"inv_table_{key_suffix}",
            )
            if loc_label != "All":
                if st.button(f"Delete all in {loc_label}", key=f"del_{key_suffix}"):
                    save_data(df[df["location"] != loc_label])
                    st.success("Deleted successfully.")

            # Single-row delete selector (safe)
            st.markdown("**Delete a single row (safe):**")
            if ROW_ID in view.columns and len(view) > 0:
                options = []
                for _, r in view.iterrows():
                    label = f"{r.get('name','')} | CAS:{r.get('cas','') or '-'} | Size:{r.get('container_size','') or '-'} | Loc:{r.get('location','') or '-'} | Bottles:{r.get('bottles','')} | ID:{str(r[ROW_ID])[:8]}"
                    options.append((label, str(r[ROW_ID])))
                labels = [lab for lab, _ in options]
                selected_label = st.selectbox("Pick row to delete", labels, key=f"rowdel_select_{key_suffix}")
                selected_id = dict(options).get(selected_label)
                if st.button("🗑️ Delete selected row", key=f"rowdel_btn_{key_suffix}", disabled=(selected_id is None)):
                    new_df = df[~df[ROW_ID].astype(str).eq(selected_id)]
                    save_data(new_df)
                    st.success("Row deleted. Refresh to see changes.")

                # ----------------- Edit single row -----------------
                st.markdown("**Edit a single row:**")
                # Reuse the same options list for labels
                options_e = options
                labels_e = labels
                selected_label_e = st.selectbox("Pick row to edit", labels_e, key=f"rowedit_select_{key_suffix}")
                selected_id_e = dict(options_e).get(selected_label_e)

                if selected_id_e:
                    match_idx = df.index[df[ROW_ID].astype(str).eq(selected_id_e)]
                    if len(match_idx) == 0:
                        st.warning("Could not find the selected row. Try refreshing.")
                    else:
                        idx = match_idx[0]
                        current_row = df.loc[idx]
                        with st.form(f"edit_form_{key_suffix}"):
                            c1, c2 = st.columns(2)
                            with c1:
                                e_name = st.text_input("Chemical Name", value=str(current_row.get("name","")), key=f"e_name_{key_suffix}")
                                e_cas = st.text_input("CAS Number", value=str(current_row.get("cas","")), key=f"e_cas_{key_suffix}")
                                e_carbons = st.text_input("Carbons", value=("" if pd.isna(current_row.get("carbons")) else str(current_row.get("carbons",""))), key=f"e_carbons_{key_suffix}")
                                e_distributor = st.text_input("Distributor", value=str(current_row.get("distributor","")), key=f"e_dist_{key_suffix}")
                                e_size = st.text_input("Container Size", value=str(current_row.get("container_size","")), key=f"e_size_{key_suffix}")
                            with c2:
                                state_options = ["Solid","Liquid","Gas","Unknown"]
                                cur_state = str(current_row.get("state","Unknown")) or "Unknown"
                                try:
                                    state_index = state_options.index(cur_state) if cur_state in state_options else 3
                                except Exception:
                                    state_index = 3
                                e_state = st.selectbox("State", state_options, index=state_index, key=f"e_state_{key_suffix}")

                                df_now = load_data()
                                locations_existing = sorted([x for x in df_now["location"].dropna().unique().tolist() if str(x).strip()])
                                default_loc = str(current_row.get("location",""))
                                initial_options = ["(new)"] + locations_existing
                                try:
                                    init_index = initial_options.index(default_loc) if default_loc in initial_options else 0
                                except Exception:
                                    init_index = 0
                                e_location_choice = st.selectbox("Storage Location", options=initial_options, index=init_index, key=f"e_loc_choice_{key_suffix}")
                                if e_location_choice == "(new)":
                                    e_location = st.text_input("Enter new location", value=default_loc, key=f"e_loc_new_{key_suffix}")
                                else:
                                    e_location = e_location_choice

                                e_bottles = st.number_input("Number of Bottles", min_value=1, value=int(current_row.get("bottles",1) or 1), key=f"e_bottles_{key_suffix}")
                                e_storage = st.text_input("Storage Conditions", value=str(current_row.get("storage_conditions","")), key=f"e_storage_{key_suffix}")
                                e_haz = st.text_area("Hazards (from SDS)", value=str(current_row.get("hazards","")), key=f"e_haz_{key_suffix}")
                                e_sds = st.text_input("Link to SDS", value=str(current_row.get("sds_link","")), key=f"e_sds_{key_suffix}")

                            save_edit = st.form_submit_button("💾 Save changes")
                        if save_edit:
                            df.at[idx, "name"] = e_name
                            df.at[idx, "cas"] = e_cas
                            if e_carbons and str(e_carbons).strip():
                                try:
                                    df.at[idx, "carbons"] = int(e_carbons)
                                except Exception:
                                    df.at[idx, "carbons"] = pd.NA
                            else:
                                df.at[idx, "carbons"] = pd.NA
                            df.at[idx, "distributor"] = e_distributor
                            df.at[idx, "container_size"] = e_size
                            df.at[idx, "state"] = e_state
                            df.at[idx, "location"] = e_location
                            df.at[idx, "bottles"] = int(e_bottles) if e_bottles else 1
                            df.at[idx, "storage_conditions"] = e_storage
                            df.at[idx, "hazards"] = e_haz
                            df.at[idx, "sds_link"] = e_sds
                            save_data(df)
                            st.success("Row updated. Switch tabs or refresh to see changes.")
            else:
                st.caption("No rows to delete in this view.")

        with tabs[0]:
            render_view(df, "All")
        for i, loc in enumerate(locations, start=1):
            with tabs[i]:
                render_view(df[df["location"] == loc], loc)
    else:
        st.info("No data in inventory yet.")

    st.download_button("⬇️ Download CSV Template", data=template_csv_bytes(), file_name="inventory_template.csv", mime="text/csv")

# ---------- Add Chemicals ----------
with t2:
    st.title("➕ Add New Chemical")
    query = st.text_input("Enter chemical name or CAS number:")
    if query:
        details = fetch_details(query)
        with st.form("add_form"):
            col1, col2 = st.columns(2)
            with col1:
                chem_name = st.text_input("Chemical Name", value=details.get("name", ""))
                cas = st.text_input("CAS Number", value=details.get("cas", ""))
                formula = st.text_input("Formula", value=details.get("formula", ""))
                carbons = st.text_input("Carbons", value=str(details.get("carbons", "N/A")))
                distributor = st.text_input("Distributor")
                container_size = st.text_input("Container Size")
            with col2:
                state = st.selectbox("State", ["Solid", "Liquid", "Gas", "Unknown"])
                df_now = load_data()
                locations_existing = sorted([x for x in df_now["location"].dropna().unique().tolist() if str(x).strip()])
                location = st.selectbox("Storage Location", options=["(new)"] + locations_existing)
                if location == "(new)":
                    location = st.text_input("Enter new location")
                bottles = st.number_input("Number of Bottles", min_value=1, value=1)
                storage_conditions = st.text_input("Storage Conditions")
                hazards = st.text_area("Hazards (from SDS)", value=details.get("hazards", ""))
                sds_link = st.text_input("Link to SDS", value=details.get("sds_link", ""))
            submitted = st.form_submit_button("Add to Inventory")
        if submitted:
            df = load_data()
            new_entry = {
                "name": chem_name,
                "cas": cas,
                "carbons": carbons if carbons else "N/A",
                "distributor": distributor,
                "container_size": container_size,
                "state": state,
                "location": location,
                "bottles": bottles,
                "storage_conditions": storage_conditions,
                "hazards": hazards,
                "sds_link": sds_link,
                ROW_ID: str(uuid.uuid4()),
            }
            df = pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True)
            save_data(df)
            st.success(f"{chem_name} added to inventory ✅")

# ---------- Upload / Merge ----------
with t3:
    st.title("📂 Upload or Merge Spreadsheet(s)")
    st.caption("Upload CSV or Excel. You can Replace, Append, or Merge/Upsert into the current inventory.")

    # --- File readers with Excel engines ---
    def _read_table(file):
        name = file.name.lower()
        ext = os.path.splitext(name)[1]
        try:
            if ext == ".csv":
                return pd.read_csv(file)
            elif ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
                return pd.read_excel(file, engine="openpyxl")
            elif ext == ".xls":
                return pd.read_excel(file, engine="xlrd")
            elif ext == ".ods":
                return pd.read_excel(file, engine="odf")
            else:
                raise ValueError(f"Unsupported file type: {ext}")
        except ImportError as e:
            st.error(
                "Missing Excel engine. Add the following to requirements.txt and redeploy:"
                "`openpyxl` (for .xlsx), `xlrd` (for .xls), `odfpy` (for .ods)."
                f"Details: {e}"
            )
            raise

    # --- Normalization helper (align columns/types) ---
    def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        for c in EXPECTED_COLS:
            if c not in df.columns:
                df[c] = pd.NA
        for c in ["name","cas","distributor","container_size","state","location","storage_conditions","hazards","sds_link"]:
            df[c] = df[c].astype(str).replace({"nan": ""}).fillna("")
        df["bottles"] = pd.to_numeric(df["bottles"], errors="coerce").fillna(1).astype(int)
        df["carbons"] = pd.to_numeric(df["carbons"], errors="coerce")
        keep_cols = EXPECTED_COLS + ([ROW_ID] if ROW_ID in df.columns else [])
        return df[keep_cols]

    def _make_keycols(df: pd.DataFrame, keys: list[str]) -> pd.Series:
        """Build a normalized composite key (case-insensitive) for matching."""
        if not keys:
            return pd.Series([""] * len(df), index=df.index)
        subset = df.reindex(columns=keys, fill_value="").astype(str)
        return subset.apply(lambda row: "::".join(str(x).strip().lower() for x in row.values), axis=1)

    # --- Uploader ---
    uploaded_files = st.file_uploader(
        "Upload CSV/XLSX/XLS/ODS",
        type=["csv", "xlsx", "xls", "xlsm", "xltx", "xltm", "ods"],
        accept_multiple_files=True,
        key="upload_merge_files",
    )

    if uploaded_files is not None and len(uploaded_files) > 0:
        frames = []
        for f in uploaded_files:
            try:
                frames.append(_read_table(f))
            except Exception as e:
                st.error(f"Failed to read {f.name}: {e}")
        if frames:
            uploaded = pd.concat(frames, ignore_index=True)
            st.subheader("Preview (first 100 rows)")
            st.dataframe(uploaded.head(100), use_container_width=True)

            # --- Apply mode ---
            mode = st.radio(
                "How should the uploaded data be applied?",
                [
                    "Replace (overwrite current inventory)",
                    "Append (add rows)",
                    "Merge/Upsert (match rows and update)",
                ],
                index=2,
            )

            current = load_data()

            # Merge settings (only shown for Merge/Upsert)
            key_cols = []
            prefer_uploaded = True
            if mode == "Merge/Upsert (match rows and update)":
                st.markdown("**Match settings**")
                avail = list(uploaded.columns)
                default_keys = [c for c in ["name", "cas", "location"] if c in avail]
                key_cols = st.multiselect(
                    "Columns to match on (choose 1+)",
                    options=avail,
                    default=default_keys if default_keys else (avail[:1] if avail else []),
                    help="Rows with the same values (case-insensitive) in these columns will be treated as the same chemical entry.",
                )
                strategy = st.selectbox(
                    "When a match is found, which data wins?",
                    [
                        "Prefer uploaded (overwrite with uploaded non-empty values)",
                        "Prefer existing (only fill blanks in current)",
                    ],
                )
                prefer_uploaded = strategy.startswith("Prefer uploaded")

            if st.button("Apply Upload", type="primary"):
                if mode.startswith("Replace"):
                    to_save = _ensure_schema(uploaded)
                    if ROW_ID not in to_save.columns:
                        to_save[ROW_ID] = [str(uuid.uuid4()) for _ in range(len(to_save))]
                    save_data(to_save)
                    st.success(f"Replaced inventory with {len(to_save)} rows.")

                elif mode.startswith("Append"):
                    up = _ensure_schema(uploaded)
                    cur = _ensure_schema(current)
                    if ROW_ID not in up.columns:
                        up[ROW_ID] = [str(uuid.uuid4()) for _ in range(len(up))]
                    combined = pd.concat([cur, up], ignore_index=True)
                    save_data(combined)
                    st.success(f"Appended {len(up)} rows (new total: {len(combined)}).")

                else:  # Merge/Upsert
                    if not key_cols:
                        st.error("Pick at least one match column for merge.")
                    else:
                        up = _ensure_schema(uploaded).copy()
                        cur = _ensure_schema(current).copy()
                        cur["__merge_key"] = _make_keycols(cur, key_cols)
                        up["__merge_key"] = _make_keycols(up, key_cols)

                        updated = 0
                        inserted = 0

                        key_to_idx = {}
                        for idx, k in cur["__merge_key"].items():
                            key_to_idx.setdefault(k, []).append(idx)

                        def _nonempty(val) -> bool:
                            if pd.isna(val):
                                return False
                            if isinstance(val, str):
                                return bool(val.strip())
                            return True

                        for _, urow in up.iterrows():
                            k = urow["__merge_key"]
                            if k in key_to_idx and len(key_to_idx[k]) > 0:
                                idx = key_to_idx[k][0]
                                changed = False
                                for c in EXPECTED_COLS:
                                    if c == ROW_ID:
                                        continue
                                    uval = urow.get(c, pd.NA)
                                    cval = cur.at[idx, c] if c in cur.columns else pd.NA
                                    if prefer_uploaded:
                                        if _nonempty(uval) and (str(uval) != str(cval)):
                                            cur.at[idx, c] = uval
                                            changed = True
                                    else:
                                        if (not _nonempty(cval)) and _nonempty(uval):
                                            cur.at[idx, c] = uval
                                            changed = True
                                if changed:
                                    updated += 1
                            else:
                                new_row = {c: urow.get(c, pd.NA) for c in EXPECTED_COLS}
                                new_row[ROW_ID] = str(uuid.uuid4())
                                cur = pd.concat([cur, pd.DataFrame([new_row])], ignore_index=True)
                                inserted += 1

                        if "__merge_key" in cur.columns:
                            cur = cur.drop(columns=["__merge_key"]) 
                        save_data(cur)
                        st.success(f"Merge complete: updated {updated}, inserted {inserted}. Total rows: {len(cur)}.")
    else:
        st.info("No files uploaded yet.")

with t4:
    st.title("⚙️ Settings & Tips")

    # Quick reset
    if st.button("Reset to blank inventory"):
        save_data(pd.DataFrame(columns=EXPECTED_COLS + [ROW_ID]))
        st.success("Inventory reset.")

    st.divider()
    st.subheader("Google Sheets backup (optional)")
    if _gsheets_enabled():
        try:
            _, sheet_url = _open_gsheets()
            st.caption(f"Connected to: {sheet_url}")
        except Exception as e:
            st.error(f"Configured, but connection failed: {e}")
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Backup now → Sheets"):
                try:
                    _backup_to_gsheets(load_data())
                    st.success("Backed up current inventory to Google Sheets.")
                except Exception as e:
                    st.error(f"Backup failed: {e}")
        with c2:
            if st.button("Restore from Sheets"):
                try:
                    restored = _restore_from_gsheets()
                    save_data(restored)
                    st.success(f"Restored {len(restored)} rows from Google Sheets.")
                except Exception as e:
                    st.error(f"Restore failed: {e}")
        with c3:
            if st.button("Test connection"):
                try:
                    _ = _open_gsheets()
                    st.info("Connection OK.")
                except Exception as e:
                    st.error(f"Connection failed: {e}")
    else:
        st.info("To enable, add `gspread` and `google-auth` to requirements, then set Streamlit Secrets with a [gsheets] block (enabled=true, spreadsheet_url, and service account JSON fields). Share the sheet with the service account email.")
