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

# with st.sidebar:
#     st.header("⚙️ Configuration Export")
#     st.subheader("Google Sheets")
#     sheet_name = st.text_input("Nom du Google Sheet", "Suivi Dépenses Conciergerie")
mail_mapping = {
    "Aurelie Goncalves": {
        "mail": "aurelie@myagency.group",
        "mail_binome": "sanaa@myagency.group"
    },
    "Fabrice Alcaud": {
        "mail": "fabrice@myagency.group",
        "mail_binome": "coline@myagency.group"
    },
    "Lara Dogliotti": {
        "mail": "lara@myagency.group",
        "mail_binome": "sofia@myagency.group"
    },
    "Mathilde Noemie Crystal Marie Amelie Bouffet": {
        "mail": "mathilde@myagency.group",
        "mail_binome": "julie@myagency.group"
    },
    "Mathile Severine Alonso": {
        "mail": "mathildea@myagency.group",
        "mail_binome": None
    },
    "Nourithe Guila Serraf": {
        "mail": "nourithe@myagency.group",
        "mail_binome": "alina@myagency.group"
    },
    "Pierre Olivier Marie Fallourd": {
        "mail": "pierref@myagency.group",
        "mail_binome": "anouchka@myagency.group"
    },
    "Ruben Abitbol": {
        "mail": "ruben@myagency.group",
        "mail_binome": "edgar@myagency.group"
    },
    "Thalia Maatouk": {
        "mail": "thalia@myagency.group",
        "mail_binome": "corporate@myagency.group"
    },
    "Vialina Glimnurova": {
        "mail": "vialina@myagency.group",
        "mail_binome": "alexandra@myagency.group"
    },
    "Yves Sauveur Abitbol": {
        "mail": "yves@myagency.group",
        "mail_binome": 'eliottdouieb@gmail.com'
    },
    "Zoe Marie Mevil": {
        "mail": "hanaa@myagency.group",
        "mail_binome": "neuilly@myagency.group"
    }
}


# ============================================================
# 2. Fonctions Utilitaires
# ============================================================

@st.cache_data
def load_data(revolut_file, bo_file):
    df_rev = pd.read_csv(revolut_file)
    df_rev['email'] = df_rev['Payer'].map(lambda x: mail_mapping.get(x, {}).get("mail"))
    df_rev['email_binome'] = df_rev['Payer'].map(lambda x: mail_mapping.get(x, {}).get("mail_binome"))


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
Si pas sûr, retourne "match non trouvé".

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

    if raw_content.startswith("```"):
        parts = raw_content.split("```")
        if len(parts) >= 2:
            raw_content = parts[1]

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
    df_rev_clean["Montant"] = pd.to_numeric(df_rev_clean["Total amount"] * (-1), errors="coerce")

    cols_rev_keep = [
        "Date", "Montant", "Description", "ID", "Type", "State",
        "Card number", "Card label", "Payer", "Exchange rate",
        "Orig currency", "Orig amount", "email","email_binome"
    ]

    df_rev_clean = df_rev_clean[cols_rev_keep]
    df_rev_clean["Libelle_match"] = df_rev_clean["Description"].map(match_libelle)
    df_rev_clean = df_rev_clean.reset_index().rename(columns={"index": "idx_rev"})

    return df_rev_clean, df_bo_clean

# Fonction Helper pour afficher les data_editor proprement
def display_interactive_table(df, key_suffix):
    """Prépare le DF pour l'édition : Ajout colonne Valide, Formatage dates, Config colonnes"""

    if df.empty:
        st.write("Aucune donnée.")
        return df

    # 1. Ajout de la colonne de validation par défautf
    df_edit = df.copy()
    df_edit.insert(0, "Valide", True)

    # 2. Configuration des colonnes
    column_config = {
        "Valide": st.column_config.CheckboxColumn(
            "Valider ?",
            help="Décochez pour rejeter ce rapprochement",
            default=True,
        ),
        "idx_rev": None,   # caché
        "idx_bo": None,   # caché
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
        disabled=[c for c in df_edit.columns if c != "Valide"]
    )

    return edited_df


# ============================================================
# 3. Logique Principale
# ============================================================

# ============================================================
# 3. Logique Principale
# ============================================================

def run_interface():
    # --- CORRECTION 1 : Initialisation des variables au début du scope ---
    # Cela empêche l'UnboundLocalError car les variables existent dès le départ.
    df_rev_clean = None
    df_bo_clean = None

    st.subheader("📥 Étape 1 : Import Revolut")
    uploaded_revolut = st.file_uploader(
        "Sélectionnez le fichier CSV Revolut",
        type=["csv"],
        key="u_rev"
    )

    uploaded_bo = None

    if uploaded_revolut:
        st.success("✅ Fichier Revolut chargé.")
        st.markdown("---")

        st.subheader("📥 Étape 2 : Import BackOffice")
        uploaded_bo = st.file_uploader(
            "Sélectionnez l'export Excel BackOffice",
            type=["xlsx"],
            key="u_bo"
        )

    # Rien d'uploadé encore
    if not uploaded_revolut:
        st.info("Veuillez commencer par charger le fichier Revolut ci-dessus.")
        return

    # Si Revolut OK mais pas encore BO, on s'arrête là
    if uploaded_revolut and not uploaded_bo:
        return

    # Ici : uploaded_revolut et uploaded_bo sont présents
    st.success("✅ Fichier BackOffice chargé. Lancement de l'analyse...")
    st.markdown("---")

    # 1. Chargement des données brutes
    df_rev_raw, df_bo_raw = load_data(uploaded_revolut, uploaded_bo)
    revolut_labels = sorted(df_rev_raw["Description"].dropna().unique().tolist())

    if "Libelle" in df_bo_raw.columns:
        backoffice_labels = sorted(df_bo_raw["Libelle"].dropna().unique().tolist())
    else:
        st.error("Colonne 'Libelle' introuvable dans le fichier BackOffice.")
        st.stop()

    # =========================
    # Gestion du state
    # =========================
    if "phase" not in st.session_state:
        st.session_state["phase"] = "mapping"   # "mapping" ou "dashboard"
    if "match_libelle" not in st.session_state:
        st.session_state["match_libelle"] = None
    if "df_rev_clean" not in st.session_state:
        st.session_state["df_rev_clean"] = None
    if "df_bo_clean" not in st.session_state:
        st.session_state["df_bo_clean"] = None

    # ============================================================
    # PHASE 1 : MAPPING IA (affiché tant que phase == "mapping")
    # ============================================================
    if st.session_state["phase"] == "mapping":

        with st.status("🤖 Analyse IA des libellés en cours...", expanded=True) as status:
            if st.session_state["match_libelle"] is None:
                match_libelle = get_ai_mapping(API_KEY, revolut_labels, backoffice_labels)
                st.session_state["match_libelle"] = match_libelle
            else:
                match_libelle = st.session_state["match_libelle"]

            status.write(match_libelle)
            status.update(
                label="IA terminée - Mapping terminé !",
                state="complete",
                expanded=False
            )

        st.info("🔎 Veuillez vérifier les correspondances proposées par l'IA avant de lancer le calcul.")

        raw_map = st.session_state["match_libelle"]
        df_mapping = pd.DataFrame(list(raw_map.items()), columns=["Libelle Revolut", "Libelle BO"])
        df_mapping.insert(0, "Valide", True)
        # On garde ceux qui ont un match ou qui sont marqués non trouvés pour vérif
        df_mapping = df_mapping[df_mapping["Libelle BO"] != "match non trouvé"]

        edited_mapping = st.data_editor(
            df_mapping,
            column_config={
                "Valide": st.column_config.CheckboxColumn("Accepter ?", default=True),
                "Libelle Revolut": st.column_config.TextColumn("Libellé Revolut", disabled=True),
                "Libelle BO": st.column_config.TextColumn("Libellé BO Suggéré", disabled=True),
            },
            use_container_width=True,
            hide_index=True,
            key="mapping_editor"
        )

        # Bouton de validation du mapping
        if st.button("✅ Valider le mapping et Lancer le Rapprochement"):
            # On travaille sur une copie du dictionnaire actuel
            updated_map = raw_map.copy()

            # On met à jour le dict en fonction des cases décochées
            for index, row in edited_mapping.iterrows():
                if row["Valide"] is False:
                    # Si l'utilisateur décoche, on force "match non trouvé"
                    updated_map[row["Libelle Revolut"]] = "match non trouvé"

            # On sauvegarde le mapping corrigé
            st.session_state["match_libelle"] = updated_map

            # On prépare les dataframes clean
            # --- CORRECTION 2 : Assignation propre sans ambiguïté ---
            df_rev_c, df_bo_c = clean_dataframes(df_rev_raw, df_bo_raw, updated_map)
            
            # Mise en session state
            st.session_state["df_rev_clean"] = df_rev_c
            st.session_state["df_bo_clean"] = df_bo_c

            # Changement de phase
            st.session_state["phase"] = "dashboard"

            # On relance
            st.rerun()

        # Tant qu'on n'a pas validé le mapping, on s'arrête là (RETURN)
        return

    # ============================================================
    # PHASE 2 : DASHBOARD & MATCHING (phase == "dashboard")
    # ============================================================

    # On récupère les objets depuis le state
    match_libelle = st.session_state["match_libelle"]
    
    # --- CORRECTION 3 : Récupération depuis le state vers les variables locales ---
    df_rev_clean = st.session_state.get("df_rev_clean")
    df_bo_clean = st.session_state.get("df_bo_clean")

    # Sécurité : si le state est vide (cas rare de refresh violent), on recalcule
    if df_rev_clean is None or df_bo_clean is None:
        df_rev_clean, df_bo_clean = clean_dataframes(df_rev_raw, df_bo_raw, match_libelle)
        st.session_state["df_rev_clean"] = df_rev_clean
        st.session_state["df_bo_clean"] = df_bo_clean

    # 3. Matching (Calcul initial)
    used_rev = set()
    used_bo = set()

    def filtre_nouveaux(df):
        return df[
            ~df["idx_rev"].isin(used_rev)
            & ~df["idx_bo"].isin(used_bo)
        ]

    def maj_sets(df):
        used_rev.update(df["idx_rev"].dropna().unique())
        used_bo.update(df["idx_bo"].dropna().unique())

    # ... (Le reste de votre code de matching reste identique) ...
    
    # MATCH 1: Parfait
    matches_ok = (
        df_rev_clean.merge(
            df_bo_clean,
            left_on=["Date", "Montant", "Libelle_match"],
            right_on=["Date", "Montant", "Libelle"],
            how="inner",
            suffixes=("_rev", "_bo")
        )
        .drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_ok)

    # MATCH 2: Sans Libellé
    matches_sans_libelle = filtre_nouveaux(
        df_rev_clean.merge(
            df_bo_clean,
            left_on=["Date", "Montant"],
            right_on=["Date", "Montant"],
            how="inner",
            suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_sans_libelle)

    # MATCH 3: Potentiel sans conversion
    m_sans_conversion = (
        df_rev_clean.merge(
            df_bo_clean,
            left_on=["Libelle_match"],
            right_on=["Libelle"],
            how="inner",
            suffixes=("_rev", "_bo")
        )
        .drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    m_sans_conversion["ecart_jours"] = (m_sans_conversion["Date_bo"] - m_sans_conversion["Date_rev"]).dt.days.abs()
    matches_potentiel_sans_conversion = filtre_nouveaux(m_sans_conversion[m_sans_conversion["ecart_jours"] <= 3])
    # Filtrages supplémentaires
    if 'Orig currency' in matches_potentiel_sans_conversion.columns:
        matches_potentiel_sans_conversion = matches_potentiel_sans_conversion[matches_potentiel_sans_conversion['Orig currency'] != 'EUR']
    if 'Exchange rate' in matches_potentiel_sans_conversion.columns:
        matches_potentiel_sans_conversion = matches_potentiel_sans_conversion[matches_potentiel_sans_conversion['Exchange rate'].isna()]
    
    maj_sets(matches_potentiel_sans_conversion)

    # MATCH 4: Sans Date
    matches_sans_date = filtre_nouveaux(
        df_rev_clean.merge(
            df_bo_clean,
            left_on=["Montant", "Libelle_match"],
            right_on=["Montant", "Libelle"],
            how="inner",
            suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_sans_date)

    # MATCH 5: Sans Montant
    matches_sans_montant = filtre_nouveaux(
        df_rev_clean.merge(
            df_bo_clean,
            left_on=["Date", "Libelle_match"],
            right_on=["Date", "Libelle"],
            how="inner",
            suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_sans_montant)

    # MATCH 6: Potentiel (écart jours)
    m_pot = (
        df_rev_clean.merge(
            df_bo_clean,
            left_on=["Libelle_match"],
            right_on=["Libelle"],
            how="inner",
            suffixes=("_rev", "_bo")
        )
        .drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    m_pot["ecart_jours"] = (m_pot["Date_bo"] - m_pot["Date_rev"]).dt.days.abs()
    matches_potentiel = filtre_nouveaux(m_pot[m_pot["ecart_jours"] <= 3])
    maj_sets(matches_potentiel)

    # KO initiaux
    matches_ko_rev_initial = df_rev_clean[~df_rev_clean["idx_rev"].isin(used_rev)]
    matches_ko_bo_initial = df_bo_clean[~df_bo_clean["idx_bo"].isin(used_bo)]

    if "ko_rev_final" not in st.session_state:
        st.session_state["ko_rev_final"] = matches_ko_rev_initial
    if "ko_bo_final" not in st.session_state:
        st.session_state["ko_bo_final"] = matches_ko_bo_initial

    # IMPORTANT: Si on change de jeu de données (reset), il faut s'assurer que les KO ne sont pas obsolètes
    # Mais ici on suppose que l'utilisateur continue sur la même lancée. 
    # Si le nombre de KO est nul alors qu'on vient de calculer des initiaux non nuls, on reset.
    if len(st.session_state["ko_rev_final"]) == 0 and len(matches_ko_rev_initial) > 0:
         st.session_state["ko_rev_final"] = matches_ko_rev_initial
         st.session_state["ko_bo_final"] = matches_ko_bo_initial

    # --- AFFICHAGE DASHBOARD ---
    col1, col2, col3, col4 = st.columns(4)
    total_rev = len(df_rev_clean)
    total_matched = len(used_rev)
    percent = round((total_matched / total_rev) * 100, 1) if total_rev > 0 else 0

    col1.metric("Total Transactions", total_rev)
    col2.metric("Matchées (Init)", total_matched, f"{percent}%")
    col3.metric("KO Revolut (Actuel)", len(st.session_state["ko_rev_final"]), delta_color="inverse")
    col4.metric("KO BackOffice (Actuel)", len(st.session_state["ko_bo_final"]), delta_color="inverse")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "✅ Matches & Validation",
        "⚠️ KO Revolut (À traiter)",
        "⚠️ KO BackOffice",
        "📤 Relances des dépenses",
        "📦 Export vers Sage"
    ])

    # --- TAB 1 : Tableaux Interactifs ---
    with tab1:
        st.info("Décochez la case 'Valide ?' si un rapprochement est incorrect, puis cliquez sur 'Mettre à jour' en bas.")

        base_cols = [
            "idx_rev", "idx_bo", "Date", "Montant",
            "Description", "Libelle", "Payer",
            "Exchange rate", "Orig currency", "Orig amount",
            "email", "email_binome", "Compte"
        ]
        safe_cols = lambda df: [c for c in base_cols if c in df.columns]

        # Vues
        with st.expander(f"Matchs Parfaits ({len(matches_ok)})", expanded=True):
            edited_ok = display_interactive_table(matches_ok[safe_cols(matches_ok)], "ok")

        with st.expander(f"Matchs Sans Libellé ({len(matches_sans_libelle)})"):
            edited_sl = display_interactive_table(matches_sans_libelle[safe_cols(matches_sans_libelle)], "sl")

        with st.expander(f"Matchs Sans Date ({len(matches_sans_date)})"):
            edited_sd = display_interactive_table(matches_sans_date, "sd")
        
        with st.expander(f"Matchs Erreur Conversion ({len(matches_potentiel_sans_conversion)})"):
             edited_pot_sans_conversion = display_interactive_table(matches_potentiel_sans_conversion, "pot_sans_conv")

        with st.expander(f"Matchs Sans Montant ({len(matches_sans_montant)})"):
            edited_sm = display_interactive_table(matches_sans_montant, "sm")

        with st.expander(f"Matchs Potentiels (Date +/- 3j) ({len(matches_potentiel)})"):
            edited_pot = display_interactive_table(matches_potentiel, "pot")

        st.markdown("---")

        if st.button("🔄 Mettre à jour les KO avec les rejets"):
            all_edited = [edited_ok, edited_sl, edited_sd, edited_pot_sans_conversion, edited_sm, edited_pot]
            
            rejected_rev_ids = []
            rejected_bo_ids = []

            for df in all_edited:
                if not df.empty and "Valide" in df.columns:
                    rejected = df[df["Valide"] == False]
                    if not rejected.empty:
                        if "idx_rev" in rejected.columns:
                            rejected_rev_ids.extend(rejected["idx_rev"].tolist())
                        if "idx_bo" in rejected.columns:
                            rejected_bo_ids.extend(rejected["idx_bo"].tolist())

            rows_to_add_rev = df_rev_clean[df_rev_clean["idx_rev"].isin(rejected_rev_ids)]
            rows_to_add_bo = df_bo_clean[df_bo_clean["idx_bo"].isin(rejected_bo_ids)]

            current_ko_rev = pd.concat([matches_ko_rev_initial, rows_to_add_rev]).drop_duplicates(subset="idx_rev")
            current_ko_bo = pd.concat([matches_ko_bo_initial, rows_to_add_bo]).drop_duplicates(subset="idx_bo")

            st.session_state["ko_rev_final"] = current_ko_rev
            st.session_state["ko_bo_final"] = current_ko_bo

            st.success(f"Mise à jour effectuée ! {len(rejected_rev_ids)} rapprochements rejetés.")

    # --- TAB 2 : KO Revolut ---
    with tab2:
        df_ko_rev = st.session_state["ko_rev_final"]
        st.dataframe(df_ko_rev)
        
        csv_ko = df_ko_rev.to_csv(index=False).encode("utf-8")
        st.download_button("Télécharger CSV KO Revolut", data=csv_ko, file_name="revolut_ko.csv", mime="text/csv")

    # --- TAB 3 : KO BackOffice ---
    with tab3:
        st.dataframe(st.session_state["ko_bo_final"])

    # --- TAB 4 : Relances ---
    with tab4:
        st.header("📤 Relances des dépenses incomplètes")
        st.markdown("""
        **Important :**
        - En cliquant ci-dessous, vous envoyez les données vers le Google Sheet de suivi.
        👉 [Lien vers le Sheet](https://docs.google.com/spreadsheets/d/1ajBDscFnvEez97iu5fDL7rZe9VI3bH9yHs-oXfDpE_I)
        """)

        if "gcp_service_account" in st.secrets:
            if st.button("🚀 Relances des dépenses incomplètes"):
                try:
                    creds_dict = dict(st.secrets["gcp_service_account"])
                    gc = gspread.service_account_from_dict(creds_dict)
                    # Assurez-vous que 'sheet_name' est défini (variable globale ou input)
                    # Ici on utilise une string en dur ou la variable globale définie plus haut
                    sh = gc.open("Suivi Dépenses Conciergerie") 

                    try:
                        ws = sh.worksheet("A traiter")
                    except:
                        ws = sh.get_worksheet(0)

                    df_export = st.session_state["ko_rev_final"].copy()
                    cols_export = [
                        "Date", "Description", "Montant",
                        "ID", "Payer", "Exchange rate",
                        "Orig currency", "Orig amount", "email", "email_binome"
                    ]
                    cols_final = [c for c in cols_export if c in df_export.columns]
                    df_export = df_export[cols_final]

                    if "Date" in df_export.columns:
                        df_export["Date"] = df_export["Date"].dt.strftime("%Y-%m-%d")

                    df_export = df_export.fillna("")

                    ws.insert_rows(df_export.values.tolist(), row=2)
                    st.success(f"✅ {len(df_export)} lignes exportées avec succès !")

                except Exception as e:
                    st.error(f"Erreur export : {e}")
        else:
            st.warning("⚠️ Secrets GCP manquants.")