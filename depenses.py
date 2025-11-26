import streamlit as st
import pandas as pd
import json
import os
from io import StringIO
from xlsx2csv import Xlsx2csv
from openai import OpenAI
import gspread

# ============================================================
# 0. Configuration & Secrets
# ============================================================
st.set_page_config(
    page_title="Rapprochement Bancaire IA",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .stMetric { background-color: #f0f2f6; padding: 10px; border-radius: 10px; }
    .block-container { padding-top: 2rem; }
    </style>
    """, unsafe_allow_html=True)

st.title("💳 Rapprochement Bancaire Intelligent")
st.markdown("---")

try:
    API_KEY = st.secrets["crm"]["api_key"]
except Exception:
    st.error("❌ Clé API OpenAI manquante dans secrets.toml")
    st.stop()

# ============================================================
# 1. Sidebar
# ============================================================
with st.sidebar:
    st.header("⚙️ Configuration")
    sheet_name = st.text_input("Nom du Google Sheet", "Suivi Dépenses Conciergerie")
    mail_mapping = {"Yves Sauveur Abitbol": "eliottdouieb@gmail.com"}
    
    st.markdown("---")
    if st.button("🗑️ Réinitialiser tout"):
        st.session_state.clear()
        st.rerun()

# ============================================================
# 2. Fonctions
# ============================================================

@st.cache_data
def load_data(revolut_file, bo_file):
    df_rev = pd.read_csv(revolut_file)
    df_rev['email'] = df_rev['Payer'].map(mail_mapping)
    
    buffer = StringIO()
    Xlsx2csv(bo_file, outputencoding="utf-8").convert(buffer)
    buffer.seek(0)
    df_bo = pd.read_csv(buffer, skiprows=1)
    
    return df_rev, df_bo

@st.cache_data
def get_ai_mapping(api_key, rev_labels, bo_labels):
    if not api_key: return {}
    client = OpenAI(api_key=api_key)
    prompt = f"""
    Objectif: Associer les libellés Revolut aux libellés BackOffice.
    Retourne JSON: {{"Label Rev": "Label BO"}}
    Si doute: "match non trouvé".
    Rev: {json.dumps(rev_labels, ensure_ascii=False)}
    BO: {json.dumps(bo_labels, ensure_ascii=False)}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        content = response.choices[0].message.content
        if "```" in content: content = content.split("```")[1].replace("json", "")
        return json.loads(content)
    except:
        return {}

def clean_and_match_logic(df_rev_raw, df_bo_raw, match_libelle):
    # 1. Cleaning
    df_bo = df_bo_raw.copy()
    df_bo["Date"] = pd.to_datetime(df_bo["Date"], errors="coerce")
    df_bo["Montant"] = df_bo["Débit(€)"] - df_bo["Crédit (€)"]
    df_bo = df_bo[df_bo["Montant"] > 0].reset_index().rename(columns={"index": "idx_bo"})

    df_rev = df_rev_raw.copy()
    df_rev = df_rev[df_rev["Type"] == "CARD_PAYMENT"]
    df_rev["Date"] = pd.to_datetime(df_rev["Date started (UTC)"], errors="coerce")
    df_rev["Montant"] = pd.to_numeric(df_rev["Amount"]*(-1), errors="coerce")
    df_rev["Libelle_match"] = df_rev["Description"].map(match_libelle)
    df_rev = df_rev.reset_index().rename(columns={"index": "idx_rev"})
    
    # 2. Matching Logic
    used_rev, used_bo = set(), set()
    def maj(df):
        used_rev.update(df["idx_rev"].unique())
        used_bo.update(df["idx_bo"].unique())
    def get_new(df): 
        return df[~df["idx_rev"].isin(used_rev) & ~df["idx_bo"].isin(used_bo)]

    # Algorithmes
    matches_ok = df_rev.merge(df_bo, left_on=["Date", "Montant", "Libelle_match"], right_on=["Date", "Montant", "Libelle"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"])
    maj(matches_ok)

    matches_sl = get_new(df_rev.merge(df_bo, left_on=["Date", "Montant"], right_on=["Date", "Montant"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"]))
    maj(matches_sl)

    matches_sd = get_new(df_rev.merge(df_bo, left_on=["Montant", "Libelle_match"], right_on=["Montant", "Libelle"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"]))
    maj(matches_sd)

    matches_sm = get_new(df_rev.merge(df_bo, left_on=["Date", "Libelle_match"], right_on=["Date", "Libelle"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"]))
    maj(matches_sm)

    m_pot = df_rev.merge(df_bo, left_on=["Libelle_match"], right_on=["Libelle"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"])
    m_pot["ecart_jours"] = (m_pot["Date_bo"] - m_pot["Date_rev"]).dt.days.abs()
    matches_pot = get_new(m_pot[m_pot["ecart_jours"] <= 3])
    maj(matches_pot)

    # KOs
    ko_rev = df_rev[~df_rev["idx_rev"].isin(used_rev)]
    ko_bo = df_bo[~df_bo["idx_bo"].isin(used_bo)]

    return {
        "ok": matches_ok, "sl": matches_sl, "sd": matches_sd, "sm": matches_sm, "pot": matches_pot,
        "ko_rev": ko_rev, "ko_bo": ko_bo,
        "full_rev": df_rev, "full_bo": df_bo
    }

def display_interactive_table(df, key_suffix):
    if df.empty:
        st.write("Aucune donnée.")
        return df
    df_edit = df.copy()
    df_edit.insert(0, "Valide", True)
    
    column_config = {
        "Valide": st.column_config.CheckboxColumn("Valider ?", default=True),
        "idx_rev": None, "idx_bo": None,
        "Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
        "Date_rev": st.column_config.DateColumn("Date Rev", format="DD/MM/YYYY"),
        "Date_bo": st.column_config.DateColumn("Date BO", format="DD/MM/YYYY"),
        "Montant": st.column_config.NumberColumn("Montant", format="%.2f €"),
        "Description": "Libellé BO", "Libelle": "Libellé Rev"
    }
    
    return st.data_editor(
        df_edit, column_config=column_config, 
        use_container_width=True, hide_index=True, key=f"edit_{key_suffix}",
        disabled=[c for c in df_edit.columns if c != "Valide"]
    )

# ============================================================
# 3. Logique Principale
# ============================================================
def run_interface():
    
    # --- ETAPE 0 : UPLOAD ---
    st.subheader("📥 Imports")
    c1, c2 = st.columns(2)
    u_rev = c1.file_uploader("Revolut (CSV)", type=["csv"], key="u_rev")
    u_bo = c2.file_uploader("BackOffice (XLSX)", type=["xlsx"], key="u_bo")
    
    # Reset si pas de fichiers
    if not u_rev or not u_bo:
        st.session_state['app_phase'] = 'upload'
        st.info("Veuillez charger les deux fichiers.")
        return

    # Initialisation de la phase si nouvelle
    if 'app_phase' not in st.session_state:
        st.session_state['app_phase'] = 'mapping'

    # Chargement des données brutes (toujours nécessaire)
    df_rev_raw, df_bo_raw = load_data(u_rev, u_bo)

    # --- ETAPE 1 : MAPPING IA (Seulement si phase 'mapping') ---
    if st.session_state['app_phase'] == 'mapping':
        
        # 1. Calcul IA (si pas encore fait)
        if 'ai_result_raw' not in st.session_state:
            with st.status("🤖 IA en cours..."):
                rev_lbls = sorted(df_rev_raw["Description"].dropna().unique().tolist())
                if "Libelle" not in df_bo_raw.columns:
                    st.error("Colonne 'Libelle' manquante dans BackOffice"); st.stop()
                bo_lbls = sorted(df_bo_raw["Libelle"].dropna().unique().tolist())
                st.session_state['ai_result_raw'] = get_ai_mapping(API_KEY, rev_lbls, bo_lbls)

        # 2. Editeur de Mapping
        st.info("🔎 Vérifiez les correspondances avant validation.")
        raw_map = st.session_state['ai_result_raw']
        df_map = pd.DataFrame(list(raw_map.items()), columns=["Libelle Revolut", "Libelle BO"])
        df_map.insert(0, "Valide", True)
        df_map = df_map[df_map["Libelle BO"] != "match non trouvé"]

        edited_map = st.data_editor(
            df_map, 
            column_config={"Valide": st.column_config.CheckboxColumn("OK?", default=True)},
            use_container_width=True, hide_index=True, key="map_editor"
        )

        # 3. BOUTON VALIDATION -> CALCUL -> TRANSITION
        if st.button("✅ Valider et Lancer les Calculs"):
            # A. Création du dict final
            final_map = raw_map.copy()
            for i, row in edited_map.iterrows():
                if not row["Valide"]: final_map[row["Libelle Revolut"]] = "match non trouvé"
            
            # B. Exécution UNIQUE des calculs
            with st.spinner("Calcul des rapprochements..."):
                results = clean_and_match_logic(df_rev_raw, df_bo_raw, final_map)
            
            # C. Stockage des résultats dans Session State
            st.session_state['results'] = results
            st.session_state['ko_rev_final'] = results['ko_rev']
            st.session_state['ko_bo_final'] = results['ko_bo']
            
            # D. Changement de phase
            st.session_state['app_phase'] = 'dashboard'
            st.rerun()

    # --- ETAPE 2 : DASHBOARD (Seulement si phase 'dashboard') ---
    elif st.session_state['app_phase'] == 'dashboard':
        
        res = st.session_state['results']
        
        # Bouton retour
        if st.button("↩️ Modifier le mapping"):
            st.session_state['app_phase'] = 'mapping'
            st.rerun()
            
        st.success("✅ Rapprochement effectué.")
        
        # KPIs
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total", len(res['full_rev']))
        c2.metric("Matchées", len(res['full_rev']) - len(st.session_state['ko_rev_final']))
        c3.metric("KO Revolut", len(st.session_state['ko_rev_final']), delta_color="inverse")
        c4.metric("KO BackOffice", len(st.session_state['ko_bo_final']), delta_color="inverse")

        t1, t2, t3, t4 = st.tabs(["✅ Matchs", "⚠️ KO Revolut", "⚠️ KO BO", "📤 Export"])
        
        with t1:
            st.caption("Décochez pour rejeter un match.")
            
            # Affichage des tables (Lecture seule des données 'res', écriture dans 'edited_...')
            cols_base = ['idx_rev', 'idx_bo', 'Date', 'Montant', 'Description', 'Libelle', 'Payer', 'email']
            cols_date = ['idx_rev', 'idx_bo', 'Date_rev', 'Date_bo', 'Montant', 'Description', 'Libelle']
            cols_montant = ['idx_rev', 'idx_bo', 'Date', 'Montant_rev', 'Montant_bo', 'Description', 'Libelle']

            with st.expander(f"Parfaits ({len(res['ok'])})"):
                e_ok = display_interactive_table(res['ok'][ [c for c in cols_base if c in res['ok'].columns] ], "ok")
            with st.expander(f"Sans Libellé ({len(res['sl'])})"):
                e_sl = display_interactive_table(res['sl'][ [c for c in cols_base if c in res['sl'].columns] ], "sl")
            with st.expander(f"Sans Date ({len(res['sd'])})"):
                e_sd = display_interactive_table(res['sd'][ [c for c in cols_date if c in res['sd'].columns] ], "sd")
            with st.expander(f"Sans Montant ({len(res['sm'])})"):
                e_sm = display_interactive_table(res['sm'][ [c for c in cols_montant if c in res['sm'].columns] ], "sm")
            with st.expander(f"Potentiels ({len(res['pot'])})"):
                e_pot = display_interactive_table(res['pot'][ [c for c in cols_date if c in res['pot'].columns] ], "pot")

            st.markdown("---")
            
            # --- BOUTON UPDATE (Logique interne au dashboard) ---
            if st.button("🔄 Mettre à jour les KO"):
                rej_rev, rej_bo = [], []
                for df in [e_ok, e_sl, e_sd, e_sm, e_pot]:
                    if not df.empty and "Valide" in df.columns:
                        rejet = df[df["Valide"]==False]
                        if not rejet.empty:
                            if "idx_rev" in rejet: rej_rev.extend(rejet["idx_rev"].tolist())
                            if "idx_bo" in rejet: rej_bo.extend(rejet["idx_bo"].tolist())
                
                # Ajout aux KO existants
                new_ko_rev = res['full_rev'][res['full_rev']['idx_rev'].isin(rej_rev)]
                new_ko_bo = res['full_bo'][res['full_bo']['idx_bo'].isin(rej_bo)]
                
                # Mise à jour Session State
                st.session_state['ko_rev_final'] = pd.concat([st.session_state['ko_rev_final'], new_ko_rev]).drop_duplicates(subset="idx_rev")
                st.session_state['ko_bo_final'] = pd.concat([st.session_state['ko_bo_final'], new_ko_bo]).drop_duplicates(subset="idx_bo")
                
                st.success(f"{len(rej_rev)} lignes rejetées ajoutées aux KO.")
                st.rerun()

        with t2:
            st.dataframe(st.session_state['ko_rev_final'])
            csv = st.session_state['ko_rev_final'].to_csv(index=False).encode('utf-8')
            st.download_button("CSV KO", csv, "ko_rev.csv", "text/csv")
            
        with t3:
            st.dataframe(st.session_state['ko_bo_final'])

        with t4:
            if "gcp_service_account" in st.secrets:
                if st.button("🚀 Exporter vers GSheet"):
                    try:
                        creds = dict(st.secrets["gcp_service_account"])
                        gc = gspread.service_account_from_dict(creds)
                        sh = gc.open(sheet_name)
                        try: ws = sh.worksheet("A traiter")
                        except: ws = sh.get_worksheet(0)
                        
                        df_ex = st.session_state['ko_rev_final'].copy()
                        if "Date" in df_ex: df_ex["Date"] = df_ex["Date"].dt.strftime("%Y-%m-%d")
                        df_ex = df_ex.fillna("")
                        
                        # Insertion ligne 2
                        ws.insert_rows(df_ex.values.tolist(), row=2)
                        st.success(f"{len(df_ex)} lignes exportées !")
                    except Exception as e:
                        st.error(f"Erreur: {e}")
            else:
                st.warning("Secrets GCP manquants")

if __name__ == "__main__":
    run_interface()