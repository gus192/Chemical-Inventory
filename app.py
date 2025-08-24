import streamlit as st
import pandas as pd
import os
from io import BytesIO
import requests, re

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

def template_csv_bytes():
    buf = BytesIO()
    pd.DataFrame(columns=EXPECTED_COLS).to_csv(buf, index=False)
    buf.seek(0)
    return buf

def _normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in EXPECTED_COLS:
        if c not in df.columns:
            df[c] = pd.NA
    for c in ["name","cas","distributor","container_size","state","location","storage_conditions","hazards","sds_link"]:
        df[c] = df[c].astype(str).replace({"nan":""}).fillna("")
    df["bottles"] = pd.to_numeric(df["bottles"], errors="coerce").fillna(1).astype(int)
    df["carbons"] = pd.to_numeric(df["carbons"], errors="coerce")
    return df[EXPECTED_COLS]

def load_data() -> pd.DataFrame:
    if os.path.exists(DATA_FILE):
        try:
            return _normalize_df(pd.read_csv(DATA_FILE))
        except Exception:
            return pd.DataFrame(columns=EXPECTED_COLS)
    return pd.DataFrame(columns=EXPECTED_COLS)

def save_data(df: pd.DataFrame):
    _normalize_df(df).to_csv(DATA_FILE, index=False)

# ---------- Smarter external lookup (PubChem PUG-REST + PUG-View) ----------
def _is_cas(s: str) -> bool:
    s = s.strip()
    return bool(re.fullmatch(r"\d{2,7}-\d{2}-\d", s))

def _pick_common_name(query: str, syns: list[str]) -> str:
    if not syns:
        return query
    pri_words = ["acid","alcohol","oxide","chloride","hydroxide","benzene","acetone","toluene",
                 "ethyl","methyl","propyl","butyl","hexane","heptane","octane","polymer"]
    ql = query.lower().strip()
    if not _is_cas(query):
        for s in syns:
            if s.lower() == ql:
                return s
    ranked = sorted(syns, key=lambda s: (0 if any(w in s.lower() for w in pri_words) else 1, len(s)))
    for s in ranked:
        if "hydrochloric acid" in s.lower():
            return s
    return ranked[0]

def fetch_details(query: str):
    """Return dict: name, cas, formula, carbons, hazards(text), sds_link."""
    try:
        # Resolve CID
        if _is_cas(query):
            r = requests.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/xref/RN/{query}/cids/JSON",
                timeout=10,
            )
        else:
            r = requests.get(
                f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{requests.utils.quote(query)}/cids/JSON",
                timeout=10,
            )
        r.raise_for_status()
        cids = r.json().get("IdentifierList", {}).get("CID", [])
        if not cids:
            raise RuntimeError("No CID found")
        cid = cids[0]

        # PUG-View rich record
        rv = requests.get(
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON",
            timeout=10,
        )
        rv.raise_for_status()
        data = rv.json()

        synonyms, formula, hazards, sds_link, cas_from_record = [], None, [], None, None

        def walk(sections):
            nonlocal synonyms, formula, hazards, sds_link, cas_from_record
            for sec in sections:
                toc = sec.get("TOCHeading", "")
                if toc in ("Names and Identifiers", "Synonyms"):
                    for s2 in (sec.get("Section") or []):
                        if s2.get("TOCHeading") in ("Synonyms", "Depositor-Supplied Synonyms", "Other Names"):
                            for it in (s2.get("Information") or []):
                                synonyms.extend((it.get("StringList") or {}).get("String", []) or [])
                        if s2.get("TOCHeading") == "Molecular Formula":
                            for it in (s2.get("Information") or []):
                                formula = it.get("StringValue", formula)
                        if s2.get("TOCHeading") == "CAS":
                            for it in (s2.get("Information") or []):
                                cas_from_record = it.get("StringValue", cas_from_record)
                if toc in ("Safety and Hazards", "GHS Classification"):
                    for s2 in (sec.get("Section") or []):
                        if s2.get("TOCHeading") == "GHS Classification":
                            for it in (s2.get("Information") or []):
                                for item in it.get("StringWithMarkup", []) or []:
                                    txt = (item.get("String") or "").strip()
                                    if txt and txt not in hazards:
                                        hazards.append(txt)
                if toc == "Safety and Hazards":
                    for s2 in (sec.get("Section") or []):
                        if s2.get("TOCHeading") in ("Safety Sources", "Safety and Hazards - SDS"):
                            for it in (s2.get("Information") or []):
                                for m in it.get("Reference", []) or []:
                                    url = m.get("URL")
                                    if url and not sds_link:
                                        sds_link = url
            for sec in sections:
                if sec.get("Section"):
                    walk(sec["Section"])

        walk(data.get("Record", {}).get("Section", []) or [])

        common_name = _pick_common_name(query, synonyms) if synonyms else query
        if _is_cas(query) and not cas_from_record:
            cas_from_record = query

        carbons = None
        if formula:
            m = re.search(r"C(\d+)", formula)
            if m:
                carbons = int(m.group(1))

        if not sds_link:
            sds_link = f"https://www.google.com/search?q={requests.utils.quote((common_name or query) + ' SDS')}"

        return {
            "name": common_name or query,
            "cas": cas_from_record or "",
            "formula": formula or "",
            "carbons": carbons,
            "hazards": "\n".join(hazards[:20]) if hazards else "",
            "sds_link": sds_link,
        }
    except Exception:
        return {
            "name": query,
            "cas": "",
            "formula": "",
            "carbons": None,
            "hazards": "",
            "sds_link": f"https://www.google.com/search?q={requests.utils.quote(query + ' SDS')}",
        }

# =============================
# UI Tabs
# =============================

t1, t2, t3, t4 = st.tabs(["Inventory", "Add Chemicals", "Upload / Merge", "Settings"])

# ---------- Inventory ----------
with t1:
    st.title("🔬 Neitzel Lab Inventory")
    df = load_data()

    # --- Global search at the top ---
    search_q = st.text_input("Search (name/CAS/hazards)")

    if len(df) > 0:
        locations = sorted([x for x in df["location"].dropna().unique().tolist() if str(x).strip()])
        sub_tabs = st.tabs(["All"] + locations)

        def render_view(view_df, loc_label):
            view = view_df.copy()
            if search_q:
                ql = search_q.lower()
                view = view[view.apply(lambda r: any(ql in str(r[c]).lower() for c in ["name","cas","hazards","location","distributor"]), axis=1)]
            display = view.copy()
            display["carbons"] = display["carbons"].apply(lambda x: int(x) if pd.notna(x) else "N/A")
            display["state"] = display["state"].apply(lambda x: x if str(x).strip() else "N/A")
            from streamlit import column_config
            st.data_editor(
                display,
                use_container_width=True,
                disabled=True,
                column_config={ "sds_link": column_config.LinkColumn("SDS", display_text="SDS") },
            )
            if st.button(f"Delete all in {loc_label}"):
                if loc_label == "All":
                    save_data(pd.DataFrame(columns=EXPECTED_COLS))
                else:
                    save_data(df[df["location"] != loc_label])
                st.success("Deleted successfully.")

        with sub_tabs[0]:
            render_view(df, "All")
        for i, loc in enumerate(locations, start=1):
            with sub_tabs[i]:
                render_view(df[df["location"] == loc], loc)
    else:
        st.info("No data in inventory yet.")

    st.download_button("⬇️ Download CSV Template", data=template_csv_bytes(), file_name="inventory_template.csv", mime="text/csv")

# ---------- Add Chemicals ----------
with t2:
    st.title("➕ Add New Chemical")

    # Build storage location dropdown from existing data
    _df_current = load_data()
    existing_locations = sorted([x for x in _df_current["location"].dropna().unique().tolist() if str(x).strip()])
    location_choices = ["(choose)"] + existing_locations + ["+ Add new location…"]

    query = st.text_input("Enter chemical name or CAS number:")
    if query:
        details = fetch_details(query)
        st.caption("Pulled from PubChem (synonyms, formula, hazards, SDS when available). Everything is editable.")

        with st.form("add_form"):
            left, right = st.columns(2)

            with left:
                chem_name = st.text_input("Chemical Name", value=details.get("name", ""))
                cas = st.text_input("CAS Number", value=details.get("cas", ""))
                formula = st.text_input("Formula", value=details.get("formula", ""))
                carbons_prefill = details.get("carbons")
                carbons_str = "" if carbons_prefill is None else str(carbons_prefill)
                carbons = st.text_input("Carbons", value=carbons_str, help="Leave blank if Not Applicable")
                hazards_txt = st.text_area("Hazards (GHS)", value=details.get("hazards", ""), height=140)
                sds_link = st.text_input("SDS Link", value=details.get("sds_link", ""))

            with right:
                distributor = st.text_input("Distributor")
                container_size = st.text_input("Container Size")
                state = st.selectbox("State", ["Solid", "Liquid", "Gas", "Unknown"], index=3)
                loc_choice = st.selectbox("Storage Location", location_choices)
                new_loc = ""
                if loc_choice == "+ Add new location…":
                    new_loc = st.text_input("New location name")
                bottles = st.number_input("Number of Bottles", min_value=1, value=1)
                storage_conditions = st.text_input("Storage Conditions")

            submitted = st.form_submit_button("Add to Inventory")

        if submitted:
            final_location = new_loc.strip() if loc_choice == "+ Add new location…" else ("" if loc_choice == "(choose)" else loc_choice)
            carb_val = None
            if carbons and carbons.strip():
                try:
                    carb_val = int(carbons)
                except Exception:
                    carb_val = None

            df = load_data()
            new_entry = {
                "name": chem_name or "",
                "cas": cas or "",
                "carbons": carb_val,
                "distributor": distributor or "",
                "container_size": container_size or "",
                "state": state or "",
                "location": final_location,
                "bottles": int(bottles) if bottles else 1,
                "storage_conditions": storage_conditions or "",
                "hazards": hazards_txt or "",
                "sds_link": sds_link or "",
            }
            df = pd.concat([df, pd.DataFrame([new_entry])], ignore_index=True)
            save_data(df)
            st.success(f"{chem_name} added to inventory ✅")

# ---------- Upload / Merge ----------
with t3:
    st.title("📂 Upload or Merge Spreadsheet(s)")
    st.caption("Drop in CSV or Excel files.")
    upl = st.file_uploader("Upload CSV/XLSX", type=["csv","xlsx"], accept_multiple_files=True)
    if upl:
        frames = []
        for f in upl:
            if f.name.lower().endswith(".csv"):
                raw = pd.read_csv(f)
            else:
                raw = pd.read_excel(f)
            frames.append(raw)
        merged = pd.concat(frames, ignore_index=True)
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
