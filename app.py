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
    st.caption("Drop in CSV or Excel files. Supported: CSV, XLSX/XLSM/XLTX/XLTM, XLS, ODS.")

    def _read_excel_smart(file):
        name = file.name.lower()
        ext = os.path.splitext(name)[1]
        try:
            if ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
                return pd.read_excel(file, engine="openpyxl")
            elif ext == ".xls":
                return pd.read_excel(file, engine="xlrd")
            elif ext == ".ods":
                return pd.read_excel(file, engine="odf")
            else:
                raise ValueError(f"Unsupported Excel extension: {ext}")
        except ImportError as e:
            st.error(
                "Missing Excel engine. Add the following to requirements.txt and redeploy: "
                `openpyxl` (for .xlsx), `xlrd` (for .xls), `odfpy` (for .ods).

"
                f"Details: {e}"
            )
            raise

    upl = st.file_uploader(
        "Upload CSV/XLSX/XLS/ODS",
        type=["csv","xlsx","xls","xlsm","xltx","xltm","ods"],
        accept_multiple_files=True,
    )

    if upl:
        frames = []
        for f in upl:
            try:
                if f.name.lower().endswith(".csv"):
                    raw = pd.read_csv(f)
                else:
                    raw = _read_excel_smart(f)
                frames.append(raw)
            except Exception as e:
                st.error(f"Failed to read {f.name}: {e}")
        if frames:
            merged = pd.concat(frames, ignore_index=True)
            st.subheader("Preview (first 100 rows)")
            st.dataframe(merged.head(100), use_container_width=True)
            if st.button("Apply Upload"):
                save_data(merged)
                st.success("Upload applied ✅")

# ---------- Settings ----------
with t4:
    st.title("⚙️ Settings & Tips")
    if st.button("Reset to blank inventory"):
        save_data(pd.DataFrame(columns=EXPECTED_COLS))
        st.success("Inventory reset.")
