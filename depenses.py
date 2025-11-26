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
    .upload-step {
        border: 1px solid #e0e0e0;
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("💳 Rapprochement Bancaire Intelligent")
st.markdown("---")

# --- Récupération Sécurisée de la Clé API OpenAI ---
try:
    API_KEY = st.secrets["crm"]["api_key"]
except Exception as e:
    st.error("❌ Erreur : Impossible de récupérer la clé API OpenAI. Vérifiez [crm] api_key dans secrets.toml.")
    st.stop()

# ============================================================
# 1. Sidebar : Configuration Export
# ============================================================
with st.sidebar:
    st.header("⚙️ Configuration Export")
    st.subheader("Google Sheets")
    sheet_name = st.text_input("Nom du Google Sheet", "Suivi Dépenses Conciergerie")
    mail_mapping = {"Yves Sauveur Abitbol": "eliottdouieb@gmail.com"}

# ============================================================
# 2. Fonctions Utilitaires
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
    if not api_key: return {}
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
    if raw_content.startswith("```"):
        parts = raw_content.split("```")
        if len(parts) >= 2: raw_content = parts[1]
        raw_content = raw_content.lstrip()
        if raw_content.lower().startswith("json"):
            raw_content = raw_content.split("\n", 1)[1].lstrip()
    try:
        return json.loads(raw_content)
    except:
        return {}

def clean_dataframes(df_rev, df_bo, match_libelle):
    # BO
    df_bo_clean = df_bo.copy()
    df_bo_clean["Date"] = pd.to_datetime(df_bo_clean["Date"], errors="coerce")
    df_bo_clean["Montant"] = df_bo_clean["Débit(€)"] - df_bo_clean["Crédit (€)"]
    df_bo_clean = df_bo_clean[df_bo_clean["Montant"] > 0]
    df_bo_clean = df_bo_clean.reset_index().rename(columns={"index": "idx_bo"})

    # Revolut
    df_rev_clean = df_rev.copy()
    df_rev_clean = df_rev_clean[df_rev_clean["Type"] == "CARD_PAYMENT"]
    df_rev_clean["Date"] = pd.to_datetime(df_rev_clean["Date started (UTC)"], errors="coerce")
    df_rev_clean["Montant"] = pd.to_numeric(df_rev_clean["Amount"]*(-1), errors="coerce")
    
    cols_rev_keep = ["Date", "Montant", "Description", "ID", "Type", "State", "Card number", "Card label", "Payer", "Exchange rate", "Orig currency", "Orig amount", "email"]
    df_rev_clean = df_rev_clean[cols_rev_keep]
    df_rev_clean["Libelle_match"] = df_rev_clean["Description"].map(match_libelle)
    df_rev_clean = df_rev_clean.reset_index().rename(columns={"index": "idx_rev"})
    
    return df_rev_clean, df_bo_clean

# Fonction Helper pour afficher les data_editor proprementt
def display_interactive_table(df, key_suffix):
    """Prépare le DF pour l'édition : Ajout colonne Valide, Formatage dates, Config colonnes"""
    if df.empty:
        st.write("Aucune donnée.")
        return df

    # 1. Ajout de la colonne de validation par défaut
    df_edit = df.copy()
    df_edit.insert(0, "Valide", True)

    # 2. Configuration des colonnes pour st.data_editor
    # On cache idx_rev et idx_bo mais on les garde dans les données pour le traitement
    column_config = {
        "Valide": st.column_config.CheckboxColumn(
            "Valider ?",
            help="Décochez pour rejeter ce rapprochement",
            default=True,
        ),
        "idx_rev": None, # Caché
        "idx_bo": None,  # Caché
        "Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
        "Date_rev": st.column_config.DateColumn("Date Revolut", format="DD/MM/YYYY"),
        "Date_bo": st.column_config.DateColumn("Date BO", format="DD/MM/YYYY"),
        "Description": "Libelle BO",
        "Libelle": "Libelle Revolut",
        "Montant": st.column_config.NumberColumn("Montant", format="%.2f €"),
        "Montant_rev": st.column_config.NumberColumn("Montant Rev", format="%.2f €"),
        "Montant_bo": st.column_config.NumberColumn("Montant BO", format="%.2f €"),
    }

    # 3. Affichage
    edited_df = st.data_editor(
        df_edit,
        column_config=column_config,
        use_container_width=True,
        hide_index=True,
        key=f"editor_{key_suffix}",
        disabled=[c for c in df_edit.columns if c != "Valide"] # Seule la checkbox est modifiable
    )
    return edited_df

# ============================================================
# 3. Logique Principale
# ============================================================
def run_interface():
    
    st.subheader("📥 Étape 1 : Import Revolut")
    uploaded_revolut = st.file_uploader("Sélectionnez le fichier CSV Revolut", type=["csv"], key="u_rev")
    uploaded_bo = None
    
    if uploaded_revolut:
        st.success("✅ Fichier Revolut chargé.")
        st.markdown("---")
        st.subheader("📥 Étape 2 : Import BackOffice")
        uploaded_bo = st.file_uploader("Sélectionnez l'export Excel BackOffice", type=["xlsx"], key="u_bo")
        
    if uploaded_revolut and uploaded_bo:
        st.success("✅ Fichier BackOffice chargé. Lancement de l'analyse...")
        st.markdown("---")
        
        # 1. Chargement & IA
        df_rev_raw, df_bo_raw = load_data(uploaded_revolut, uploaded_bo)
        revolut_labels = sorted(df_rev_raw["Description"].dropna().unique().tolist())
        
        if "Libelle" in df_bo_raw.columns:
            backoffice_labels = sorted(df_bo_raw["Libelle"].dropna().unique().tolist())
        else:
            st.error("Colonne 'Libelle' introuvable dans le fichier BackOffice.")
            st.stop()

        with st.status("🤖 Analyse IA des libellés en cours...", expanded=True) as status:
            match_libelle = get_ai_mapping(API_KEY, revolut_labels, backoffice_labels)
            status.write(match_libelle)
            status.update(label="IA terminée - Mapping terminé !", state="complete", expanded=False)

        
        # 3. INTERFACE DE VÉRIFICATION DU MAPPING (NOUVELLE ÉTAPE)
        if 'final_mapping_dict' not in st.session_state:
            st.info("🔎 Veuillez vérifier les correspondances proposées par l'IA avant de lancer le calcul.")
            
            # Transformation du dict en DF pour l'éditeur
            raw_map = st.session_state['ai_mapping_raw']
            df_mapping = pd.DataFrame(list(raw_map.items()), columns=["Libelle Revolut", "Libelle BO Suggéré"])
            df_mapping.insert(0, "Valide", True)

            # Éditeur
            edited_mapping = st.data_editor(
                df_mapping,
                column_config={
                    "Valide": st.column_config.CheckboxColumn("Accepter ?", default=True),
                    "Libelle Revolut": st.column_config.TextColumn("Libellé Revolut", disabled=True),
                    "Libelle BO Suggéré": st.column_config.TextColumn("Correspondance BO", disabled=True),
                },
                use_container_width=True,
                hide_index=True,
                key="mapping_editor"
            )

            # Bouton de validation
            if st.button("✅ Valider le mapping et Lancer le Rapprochement"):
                # Construction du dictionnaire final
                final_dict = {}
                for index, row in edited_mapping.iterrows():
                    if row["Valide"]:
                        final_dict[row["Libelle Revolut"]] = row["Libelle BO Suggéré"]
                    else:
                        # Si décoché, on force "match non trouve"
                        final_dict[row["Libelle Revolut"]] = "match non trouve"
                
                st.session_state['final_mapping_dict'] = final_dict
                st.rerun() # On recharge pour passer à l'étape suivante

        # 4. Exécution du Rapprochement (Une fois le mapping validé)
        else:
            st.success("✅ Mapping validé. Calcul des rapprochements...")
            match_libelle = st.session_state['final_mapping_dict']

        # 2. Nettoyage
        df_rev_clean, df_bo_clean = clean_dataframes(df_rev_raw, df_bo_raw, match_libelle)

        # 3. Matching (Calcul initial)
        used_rev = set()
        used_bo = set()

        def filtre_nouveaux(df): return df[~df["idx_rev"].isin(used_rev) & ~df["idx_bo"].isin(used_bo)]
        def maj_sets(df):
            used_rev.update(df["idx_rev"].dropna().unique())
            used_bo.update(df["idx_bo"].dropna().unique())

        # -- Algorithmes --
        matches_ok = df_rev_clean.merge(df_bo_clean, left_on=["Date", "Montant", "Libelle_match"], right_on=["Date", "Montant", "Libelle"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"])
        maj_sets(matches_ok)

        matches_sans_libelle = filtre_nouveaux(df_rev_clean.merge(df_bo_clean, left_on=["Date", "Montant"], right_on=["Date", "Montant"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"]))
        maj_sets(matches_sans_libelle)

        matches_sans_date = filtre_nouveaux(df_rev_clean.merge(df_bo_clean, left_on=["Montant", "Libelle_match"], right_on=["Montant", "Libelle"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"]))
        maj_sets(matches_sans_date)

        matches_sans_montant = filtre_nouveaux(df_rev_clean.merge(df_bo_clean, left_on=["Date", "Libelle_match"], right_on=["Date", "Libelle"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"]))
        maj_sets(matches_sans_montant)

        m_pot = df_rev_clean.merge(df_bo_clean, left_on=["Libelle_match"], right_on=["Libelle"], how="inner", suffixes=("_rev", "_bo")).drop_duplicates(subset=["idx_rev", "idx_bo"])
        m_pot["ecart_jours"] = (m_pot["Date_bo"] - m_pot["Date_rev"]).dt.days.abs()
        matches_potentiel = filtre_nouveaux(m_pot[m_pot["ecart_jours"] <= 3])
        maj_sets(matches_potentiel)

        # KO initiaux (Calculés avant intervention utilisateur)
        matches_ko_rev_initial = df_rev_clean[~df_rev_clean["idx_rev"].isin(used_rev)]
        matches_ko_bo_initial = df_bo_clean[~df_bo_clean["idx_bo"].isin(used_bo)]

        # --- Gestion du State pour les KO finaux ---
        # On initialise les KO finaux avec les KO initiaux si c'est le premier run
        if 'ko_rev_final' not in st.session_state:
            st.session_state['ko_rev_final'] = matches_ko_rev_initial
        if 'ko_bo_final' not in st.session_state:
            st.session_state['ko_bo_final'] = matches_ko_bo_initial
            
        # Petite sécurité : si on charge de nouveaux fichiers, on reset le state
        # (On détecte le changement par la taille des DFs par exemple, ou on pourrait utiliser un ID de fichier)
        if len(st.session_state['ko_rev_final']) == 0 and len(matches_ko_rev_initial) > 0:
             st.session_state['ko_rev_final'] = matches_ko_rev_initial
             st.session_state['ko_bo_final'] = matches_ko_bo_initial


        # ============================================================
        # 4. Affichage Dashboard
        # ============================================================
        
        # KPIs (Basés sur le calcul initial, pour info)
        col1, col2, col3, col4 = st.columns(4)
        total_rev = len(df_rev_clean)
        total_matched = len(used_rev)
        percent = round((total_matched / total_rev) * 100, 1) if total_rev > 0 else 0
        
        col1.metric("Total Transactions", total_rev)
        col2.metric("Matchées (Init)", total_matched, f"{percent}%")
        col3.metric("KO Revolut (Actuel)", len(st.session_state['ko_rev_final']), delta_color="inverse")
        col4.metric("KO BackOffice (Actuel)", len(st.session_state['ko_bo_final']), delta_color="inverse")

        tab1, tab2, tab3, tab4 = st.tabs([
            "✅ Matches & Validation", 
            "⚠️ KO Revolut (A traiter)", 
            "⚠️ KO BackOffice",
            "📤 Export GSheet"
        ])

        # --- TAB 1 : Tableaux Interactifs ---
        with tab1:
            st.info("Décochez la case 'Valide ?' si un rapprochement est incorrect, puis cliquez sur 'Mettre à jour' en bas de page.")
            
            # Définition des colonnes de base à afficher (on ajoute idx_rev et idx_bo pour le tracking)
            base_cols = ['idx_rev', 'idx_bo', 'Date', 'Montant', 'Description', 'Libelle', 'Payer', 'Exchange rate', 'Orig currency', 'Orig amount', 'email', 'Compte']
            # Sécurisation si colonnes manquantes
            safe_cols = lambda df: [c for c in base_cols if c in df.columns]

            with st.expander(f"Matchs Parfaits - meme montant , meme Libellé et meme date ({len(matches_ok)})", expanded=True):
                # On prépare le DF avec les colonnes voulues
                df_ok_view = matches_ok[safe_cols(matches_ok)]
                # On affiche l'éditeur interactif
                edited_ok = display_interactive_table(df_ok_view, "ok")

            with st.expander(f"Matchs Sans Libellé - meme montant et meme date ({len(matches_sans_libelle)})"):
                df_sl_view = matches_sans_libelle[safe_cols(matches_sans_libelle)]
                edited_sl = display_interactive_table(df_sl_view, "sl")

            with st.expander(f"Matchs Sans Date - meme montant et meme Libellé ({len(matches_sans_date)})"):
                # Colonnes spécifiques pour Sans Date
                cols_sd = ['idx_rev', 'idx_bo', 'Date_rev', 'Date_bo', 'Montant', 'Description', 'Libelle', 'Payer', 'email']
                df_sd_view = matches_sans_date[[c for c in cols_sd if c in matches_sans_date.columns]]
                edited_sd = display_interactive_table(df_sd_view, "sd")

            with st.expander(f"Matchs Sans Montant - meme Libellé et meme date ({len(matches_sans_montant)})"):
                cols_sm = ['idx_rev', 'idx_bo', 'Date', 'Montant_rev', 'Montant_bo', 'Description', 'Libelle', 'Payer', 'email']
                df_sm_view = matches_sans_montant[[c for c in cols_sm if c in matches_sans_montant.columns]]
                edited_sm = display_interactive_table(df_sm_view, "sm")

            with st.expander(f"Matchs Potentiels - meme Libellé et date +- 3 jours ({len(matches_potentiel)})"):
                cols_pot = ['idx_rev', 'idx_bo', 'Date_rev', 'Date_bo', 'Montant_rev', 'Montant_bo', 'Description', 'Libelle', 'Payer', 'email']
                df_pot_view = matches_potentiel[[c for c in cols_pot if c in matches_potentiel.columns]]
                edited_pot = display_interactive_table(df_pot_view, "pot")
            
            st.markdown("---")
            # --- BOUTON DE MISE A JOUR ---
            if st.button("🔄 Mettre à jour les KO avec les rejets"):
                # 1. On rassemble tous les DataFrames édités
                all_edited = [edited_ok, edited_sl, edited_sd, edited_sm, edited_pot]
                
                rejected_rev_ids = []
                rejected_bo_ids = []
                
                for df in all_edited:
                    if not df.empty and "Valide" in df.columns:
                        # On filtre les lignes où Valide est False (décoché)
                        rejected = df[df["Valide"] == False]
                        if not rejected.empty:
                            if "idx_rev" in rejected.columns:
                                rejected_rev_ids.extend(rejected["idx_rev"].tolist())
                            if "idx_bo" in rejected.columns:
                                rejected_bo_ids.extend(rejected["idx_bo"].tolist())
                
                # 2. On récupère les lignes complètes depuis les dataframes "clean" originaux
                rows_to_add_rev = df_rev_clean[df_rev_clean["idx_rev"].isin(rejected_rev_ids)]
                rows_to_add_bo = df_bo_clean[df_bo_clean["idx_bo"].isin(rejected_bo_ids)]
                
                # 3. On met à jour le session_state
                # On prend les KO initiaux + les rejets
                # (Attention : simpliste, si on valide puis re-invalide. Idéalement on recalcule tout depuis le début, 
                # mais ici on ajoute simplement les rejets aux KO initiaux pour l'affichage)
                
                current_ko_rev = pd.concat([matches_ko_rev_initial, rows_to_add_rev]).drop_duplicates(subset="idx_rev")
                current_ko_bo = pd.concat([matches_ko_bo_initial, rows_to_add_bo]).drop_duplicates(subset="idx_bo")
                
                st.session_state['ko_rev_final'] = current_ko_rev
                st.session_state['ko_bo_final'] = current_ko_bo
                
                st.success(f"Mise à jour effectuée ! {len(rejected_rev_ids)} rapprochements rejetés.")
                st.rerun()

        # --- TAB 2 & 3 : Affichage depuis le Session State ---
        with tab2:
            st.error("Ces transactions Revolut n'ont pas trouvé de correspondance (ou ont été rejetées).")
            df_ko_rev = st.session_state['ko_rev_final']
            st.dataframe(df_ko_rev)
            
            csv_ko = df_ko_rev.to_csv(index=False).encode('utf-8')
            st.download_button("Télécharger CSV KO Revolut", data=csv_ko, file_name="revolut_ko.csv", mime="text/csv")

        with tab3:
            st.warning("Ces écritures BackOffice sont orphelines (ou rejetées).")
            st.dataframe(st.session_state['ko_bo_final'])

        with tab4:
            st.header("Export vers Google Sheets")
            if "gcp_service_account" in st.secrets:
                if st.button("🚀 Lancer l'export GSheet"):
                    try:
                        creds_dict = dict(st.secrets["gcp_service_account"])
                        gc = gspread.service_account_from_dict(creds_dict)
                        sh = gc.open(sheet_name)
                        try: ws = sh.worksheet("A traiter")
                        except: ws = sh.get_worksheet(0)
                        
                        # Export des KO finaux (incluant les rejets)
                        df_export = st.session_state['ko_rev_final'].copy()
                        cols_export = ["Date", "Description", "Montant", "ID", "Payer", "Exchange rate", "Orig currency", "Orig amount", "email"]
                        cols_final = [c for c in cols_export if c in df_export.columns]
                        df_export = df_export[cols_final]
                        if "Date" in df_export.columns:
                            df_export["Date"] = df_export["Date"].dt.strftime("%Y-%m-%d")
                        df_export = df_export.fillna("")
                        
                        # ws.append_rows(df_export.values.tolist())ggg
                        ws.insert_rows(df_export.values.tolist(), row=2)
                        st.success(f"✅ {len(df_export)} lignes exportées avec succès !")
                    except Exception as e:
                        st.error(f"Erreur export : {e}")
            else:
                st.warning("⚠️ Secrets GCP manquants.")
    
    elif not uploaded_revolut:
        st.info("Veuillez commencer par charger le fichier Revolut ci-dessus.")

