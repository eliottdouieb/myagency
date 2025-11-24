import streamlit as st
import pandas as pd
import json
import os
from io import StringIO, BytesIO
from xlsx2csv import Xlsx2csv
from openai import OpenAI
import gspread
import plotly.express as px

# ============================================================
# 0. Configuration de la page & Style & Secrets
# ============================================================
st.set_page_config(
    page_title="Rapprochement Bancaire IA",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS personnalisé
st.markdown("""
    <style>
    .stMetric {
        background-color: #f0f2f6;
        padding: 10px;
        border-radius: 10px;
    }
    .block-container {
        padding-top: 2rem;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("💳 Rapprochement Bancaire Intelligent")
st.markdown("---")

# --- Récupération Sécurisée de la Clé API ---
try:
    # On récupère la clé directement depuis les secrets Streamlit
    API_KEY = st.secrets["crm"]["api_key"]
except Exception as e:
    st.error("❌ Erreur : Impossible de récupérer la clé API dans st.secrets. Vérifiez votre fichier secrets.toml.")
    st.stop()

# ============================================================
# 1. Sidebar : Uploads Uniquement
# ============================================================
with st.sidebar:
    st.header("📂 Données")
    
    st.subheader("Import des fichiers")
    uploaded_revolut = st.file_uploader("Fichier Revolut (CSV)", type=["csv"])
    uploaded_bo = st.file_uploader("Export BackOffice (XLSX)", type=["xlsx"])
    
    st.subheader("Google Sheets (Optionnel)")
    uploaded_creds = st.file_uploader("Fichier credentials.json", type=["json"])
    sheet_name = st.text_input("Nom du Google Sheet", "Suivi Dépenses Conciergerie")
    
    # Dictionnaire Email (Code existant)
    mail_mapping = {"Yves Sauveur Abitbol": "eliottdouieb@gmail.com"}

# ============================================================
# 2. Fonctions Utilitaires (Cache & Logique)
# ============================================================

@st.cache_data
def load_data(revolut_file, bo_file):
    # --- Revolut ---
    df_rev = pd.read_csv(revolut_file)
    df_rev['email'] = df_rev['Payer'].map(mail_mapping)
    
    # --- BackOffice ---
    buffer = StringIO()
    Xlsx2csv(bo_file, outputencoding="utf-8").convert(buffer)
    buffer.seek(0)
    df_bo = pd.read_csv(buffer, skiprows=1)
    
    return df_rev, df_bo

def build_prompt(revolut_labels, backoffice_labels):
    return f"""
    Tu es un assistant spécialisé en rapprochement comptable.
    On te donne :
    1) La liste des libellés Revolut (revolut_labels).
    2) La liste des libellés Back Office (backoffice_labels).
    Objectif : Pour chaque libellé Revolut, trouve le libellé Back Office le plus probable.
    Si pas sûr, retourne "match non trouve".
    Retourne UNIQUEMENT un JSON valide : {{"Label Rev": "Label BO", ...}}
    
    Revolut labels: {json.dumps(revolut_labels, ensure_ascii=False)}
    BackOffice labels: {json.dumps(backoffice_labels, ensure_ascii=False)}
    """

@st.cache_data(show_spinner=False)
def get_ai_mapping(api_key, rev_labels, bo_labels):
    if not api_key:
        return {}
    
    client = OpenAI(api_key=api_key)
    prompt = build_prompt(rev_labels, bo_labels)
    
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Tu es un assistant expert en rapprochement comptable."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
    )
    
    raw_content = response.choices[0].message.content.strip()
    
    # Nettoyage markdown si présent
    if raw_content.startswith("```"):
        parts = raw_content.split("```")
        if len(parts) >= 2:
            raw_content = parts[1]
        raw_content = raw_content.lstrip()
        if raw_content.lower().startswith("json"):
            raw_content = raw_content.split("\n", 1)[1].lstrip()
            
    try:
        return json.loads(raw_content)
    except json.JSONDecodeError:
        st.error("Erreur lors du parsing de la réponse IA.")
        return {}

def clean_dataframes(df_rev, df_bo, match_libelle):
    # --- Clean BO ---
    df_bo_clean = df_bo.copy()
    df_bo_clean["Date"] = pd.to_datetime(df_bo_clean["Date"], errors="coerce")
    df_bo_clean["Montant"] = df_bo_clean["Débit(€)"] - df_bo_clean["Crédit (€)"]
    df_bo_clean = df_bo_clean[df_bo_clean["Montant"] > 0]
    df_bo_clean = df_bo_clean.reset_index().rename(columns={"index": "idx_bo"})

    # --- Clean Revolut ---
    df_rev_clean = df_rev.copy()
    df_rev_clean = df_rev_clean[df_rev_clean["Type"] == "CARD_PAYMENT"]
    df_rev_clean["Date"] = pd.to_datetime(df_rev_clean["Date started (UTC)"], errors="coerce")
    df_rev_clean["Montant"] = pd.to_numeric(df_rev_clean["Amount"]*(-1), errors="coerce")
    
    cols_rev_keep = [
        "Date", "Montant", "Description", "ID", "Type", "State", 
        "Card number", "Card label", "Payer", "Exchange rate", 
        "Orig currency", "Orig amount", "email"
    ]
    df_rev_clean = df_rev_clean[cols_rev_keep]
    df_rev_clean["Libelle_match"] = df_rev_clean["Description"].map(match_libelle)
    df_rev_clean = df_rev_clean.reset_index().rename(columns={"index": "idx_rev"})
    
    return df_rev_clean, df_bo_clean

# ============================================================
# 3. Logique Principale (Exécution)
# ============================================================
def run_interface():
    # On vérifie uniquement la présence des fichiers, la clé API est déjà chargée
    if uploaded_revolut and uploaded_bo:
        
        # 1. Chargement
        df_rev_raw, df_bo_raw = load_data(uploaded_revolut, uploaded_bo)
        
        # 2. Appel IA
        revolut_labels = sorted(df_rev_raw["Description"].dropna().unique().tolist())
        
        if "Libelle" in df_bo_raw.columns:
            backoffice_labels = sorted(df_bo_raw["Libelle"].dropna().unique().tolist())
        else:
            st.error("Colonne 'Libelle' introuvable dans le fichier BackOffice.")
            st.stop()

        with st.status("🤖 Analyse IA des libellés en cours...", expanded=True) as status:
            # On utilise la variable globale API_KEY chargée au début
            match_libelle = get_ai_mapping(API_KEY, revolut_labels, backoffice_labels)
            status.write("Mapping terminé !")
            status.update(label="IA terminée", state="complete", expanded=False)

        # 3. Nettoyage
        df_rev_clean, df_bo_clean = clean_dataframes(df_rev_raw, df_bo_raw, match_libelle)

        # 4. Moteur de Rapprochement (Logique inchangée)
        used_rev = set()
        used_bo = set()

        def filtre_nouveaux(df):
            return df[~df["idx_rev"].isin(used_rev) & ~df["idx_bo"].isin(used_bo)]

        def maj_sets(df):
            used_rev.update(df["idx_rev"].dropna().unique())
            used_bo.update(df["idx_bo"].dropna().unique())

        # --- Match 1: Exact ---
        matches_ok = df_rev_clean.merge(
            df_bo_clean, left_on=["Date", "Montant", "Libelle_match"], right_on=["Date", "Montant", "Libelle"],
            how="inner", suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"])
        maj_sets(matches_ok)

        # --- Match 2: Sans Libellé ---
        matches_sans_libelle = filtre_nouveaux(df_rev_clean.merge(
            df_bo_clean, left_on=["Date", "Montant"], right_on=["Date", "Montant"],
            how="inner", suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"]))
        maj_sets(matches_sans_libelle)

        # --- Match 3: Sans Date ---
        matches_sans_date = filtre_nouveaux(df_rev_clean.merge(
            df_bo_clean, left_on=["Montant", "Libelle_match"], right_on=["Montant", "Libelle"],
            how="inner", suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"]))
        maj_sets(matches_sans_date)

        # --- Match 4: Sans Montant ---
        matches_sans_montant = filtre_nouveaux(df_rev_clean.merge(
            df_bo_clean, left_on=["Date", "Libelle_match"], right_on=["Date", "Libelle"],
            how="inner", suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"]))
        maj_sets(matches_sans_montant)

        # --- Match 5: Potentiel (Date +/- 3 jours) ---
        m_pot = df_rev_clean.merge(
            df_bo_clean, left_on=["Libelle_match"], right_on=["Libelle"],
            how="inner", suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"])
        m_pot["ecart_jours"] = (m_pot["Date_bo"] - m_pot["Date_rev"]).dt.days.abs()
        matches_potentiel = filtre_nouveaux(m_pot[m_pot["ecart_jours"] <= 3])
        maj_sets(matches_potentiel)

        # --- KO ---
        matches_ko_rev = df_rev_clean[~df_rev_clean["idx_rev"].isin(used_rev)]
        matches_ko_bo = df_bo_clean[~df_bo_clean["idx_bo"].isin(used_bo)]

        # ============================================================
        # 4. Affichage Dashboard
        # ============================================================
        
        # KPIs
        col1, col2, col3, col4 = st.columns(4)
        total_rev = len(df_rev_clean)
        total_matched = len(used_rev)
        percent = round((total_matched / total_rev) * 100, 1) if total_rev > 0 else 0
        
        col1.metric("Total Transactions", total_rev)
        col2.metric("Matchées", total_matched, f"{percent}%")
        col3.metric("KO Revolut", len(matches_ko_rev), delta_color="inverse")
        col4.metric("KO BackOffice", len(matches_ko_bo), delta_color="inverse")

        # Tabs
        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "📊 Vue d'ensemble", 
            "✅ Matches Détails", 
            "⚠️ KO Revolut (A traiter)", 
            "⚠️ KO BackOffice",
            "📤 Export GSheet"
        ])

        with tab1:
            st.subheader("Répartition des méthodes de matching")
            data_dist = pd.DataFrame({
                "Méthode": ["Exact", "Sans Libellé", "Sans Date", "Sans Montant", "Potentiel"],
                "Nombre": [len(matches_ok), len(matches_sans_libelle), len(matches_sans_date), len(matches_sans_montant), len(matches_potentiel)]
            })
            fig = px.bar(data_dist, x="Méthode", y="Nombre", color="Méthode", title="Efficacité des algorithmes")
            st.plotly_chart(fig, use_container_width=True)

        with tab2:
            st.info("Voici les transactions rapprochées automatiquement.")
            with st.expander(f"Matchs Parfaits ({len(matches_ok)})", expanded=True):
                st.dataframe(matches_ok)
            with st.expander(f"Matchs Sans Libellé ({len(matches_sans_libelle)})"):
                st.dataframe(matches_sans_libelle)
            with st.expander(f"Matchs Sans Date ({len(matches_sans_date)})"):
                st.dataframe(matches_sans_date)
            with st.expander(f"Matchs Sans Montant ({len(matches_sans_montant)})"):
                st.dataframe(matches_sans_montant)
            with st.expander(f"Matchs Potentiels ({len(matches_potentiel)})"):
                st.dataframe(matches_potentiel)

        with tab3:
            st.error("Ces transactions Revolut n'ont pas trouvé de correspondance.")
            st.dataframe(matches_ko_rev)
            
            csv_ko = matches_ko_rev.to_csv(index=False).encode('utf-8')
            st.download_button("Télécharger CSV KO Revolut", data=csv_ko, file_name="revolut_ko.csv", mime="text/csv")

        with tab4:
            st.warning("Ces écritures BackOffice sont orphelines.")
            st.dataframe(matches_ko_bo)

        with tab5:
            st.header("Export vers Google Sheets")
            
            if uploaded_creds:
                if st.button("🚀 Lancer l'export GSheet"):
                    try:
                        creds_json = json.load(uploaded_creds)
                        
                        gc = gspread.service_account_from_dict(creds_json)
                        sh = gc.open(sheet_name)
                        
                        try:
                            ws = sh.worksheet("A traiter")
                        except:
                            ws = sh.get_worksheet(0)
                            
                        # Préparation Données
                        df_export = matches_ko_rev.copy()
                        cols_export = ["Date", "Description", "Montant", "ID", "Payer", "Exchange rate", "Orig currency", "Orig amount", "email"]
                        cols_final = [c for c in cols_export if c in df_export.columns]
                        df_export = df_export[cols_final]
                        if "Date" in df_export.columns:
                            df_export["Date"] = df_export["Date"].dt.strftime("%Y-%m-%d")
                        df_export = df_export.fillna("")
                        
                        ws.append_rows(df_export.values.tolist())
                        st.success(f"✅ {len(df_export)} lignes exportées avec succès dans '{sheet_name}' !")
                        
                    except Exception as e:
                        st.error(f"Erreur export : {e}")
            else:
                st.info("Veuillez uploader votre fichier `credentials.json` dans la barre latérale pour activer l'export.")

    else:
        st.info("👈 Veuillez uploader les fichiers Revolut et BackOffice pour commencer.")

