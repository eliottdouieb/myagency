import streamlit as st
import pandas as pd
import json
import os
from io import StringIO, BytesIO
from xlsx2csv import Xlsx2csv
from openai import OpenAI
import gspread
import plotly.express as px
from datetime import datetime, date
import requests
import streamlit as st




# ============================================================
# 0. Style & Secrets
# ⚠️ Aucun appel Streamlit (set_page_config, title, secrets...) au niveau global :
#    ce module est importé comme une PAGE par l'app principale, qui appelle déjà
#    st.set_page_config(). Tout le rendu se fait dans run_interface().
# ============================================================

# CSS personnalisé (injecté depuis run_interface)
PAGE_CSS = """
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
"""

# ============================================================
# 1. Sidebar : Configuration Export
# ============================================================

# with st.sidebar:
#     st.header("⚙️ Configuration Export")
#     st.subheader("Google Sheets")
sheet_name = "Suivi depenses Revolut"
mail_mapping = {
    "Aurelie Goncalves": {
        "mail": "aurelie@myagency.group",
        "mail_binome": "sanaa@myagency.group"
    },
    "Fabrice Alcaud": {
        "mail": "fabrice@myagency.group",
        "mail_binome": "coline@myagency.group"
    },
    "CB Fab": {
        "mail": "fabrice@myagency.group",
        "mail_binome": "coline@myagency.group"
    },
    "Lara Dogliotti": {
        "mail": "lara@myagency.group",
        "mail_binome": "sofia@myagency.group"
    },
    "CB LARA": {
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
    "CB MAthilde A": {
        "mail": "mathildea@myagency.group",
        "mail_binome": None
    },
    "Nourithe Guila Serraf": {
        "mail": "nourithe@myagency.group",
        "mail_binome": "alina@myagency.group"
    },
    "CB Nourithe": {
        "mail": "nourithe@myagency.group",
        "mail_binome": "alina@myagency.group"
    },
    "Pierre Olivier Marie Fallourd": {
        "mail": "pierref@myagency.group",
        "mail_binome": "anouchka@myagency.group"
    },
    "CB Pierre F": {
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
    "CB COrporate": {
        "mail": "thalia@myagency.group",
        "mail_binome": "corporate@myagency.group"
    },
    "Vialina Glimnurova": {
        "mail": "vialina@myagency.group",
        "mail_binome": "alexandra@myagency.group"
    },
    "Yves Sauveur Abitbol": {
        "mail": "yves@myagency.group",
        "mail_binome": 'sofia@myagency.group'
    },
    "Cashback Yves": {
        "mail": "yves@myagency.group",
        "mail_binome": 'sofia@myagency.group'
    },
    "Zoe Marie Mevil": {
        "mail": "hanaa@myagency.group",
        "mail_binome": "neuilly@myagency.group"
    }
}


# ============================================================
# 2. Fonctions Utilitaires
# ============================================================

def _norm_txn_id(v):
    """
    Normalise un identifiant de transaction.
    Retourne "" si l'identifiant est vide / nul / égal à 0 (cas où il faut
    retomber sur le matching classique). Gère aussi les floats type "12345.0".
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if s.lower() in ("", "0", "0.0", "nan", "none"):
        return ""
    # Cas des floats exportés par Excel : "12345.0" -> "12345"
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


@st.cache_data
def load_data(revolut_file, bo_file):
    df_rev = pd.read_csv(revolut_file)

    cashback_labels = [
    "CB LARA", "CB MAthilde A", "CB Fab", "CB Nourithe",
    "CB Pierre F", "Cashback Yves", "CB COrporate"
    ]
    mask = df_rev["Card label"].isin(cashback_labels)
    df_rev.loc[mask, ["Payer", "Card label"]] = df_rev.loc[mask, ["Card label", "Payer"]].values

    df_rev['email'] = df_rev['Payer'].map(lambda x: mail_mapping.get(x, {}).get("mail"))
    df_rev['email_binome'] = df_rev['Payer'].map(lambda x: mail_mapping.get(x, {}).get("mail_binome"))

    # ✅ Identifiant de transaction Revolut = colonne C (3e colonne du CSV)
    rev_id_col = df_rev.columns[2]
    df_rev["RevTxnId"] = df_rev[rev_id_col].map(_norm_txn_id)

    buffer = StringIO()
    Xlsx2csv(bo_file, outputencoding="utf-8").convert(buffer)
    buffer.seek(0)
    df_bo = pd.read_csv(buffer, skiprows=1)

    # ✅ Identifiant de transaction BO = colonne "dTransactionId" (colonne M)
    if "dTransactionId" in df_bo.columns:
        df_bo["BoTxnId"] = df_bo["dTransactionId"].map(_norm_txn_id)
    else:
        # fallback par position : colonne M = 13e colonne (index 12)
        if df_bo.shape[1] > 12:
            df_bo["BoTxnId"] = df_bo.iloc[:, 12].map(_norm_txn_id)
        else:
            df_bo["BoTxnId"] = ""

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


# @st.cache_data(show_spinner=False)
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


currency_symbols = {
    "€": "EUR",        # Euros
    "$": "USD",        # Dollars américains
    "£": "GBP",        # Livres sterling
    "¥": "JPY",        # Yen japonais
    "TBAT": "THB",     # Baht thaïlandais
    "$AUD": "AUD",     # Dollar australien
    "CHF": "CHF",      # Franc suisse
    "$C": "CAD",        # Dollar canadien
    "ILS": "ILS",      # Shekel israélien
    "FT": "HUF",       # Forint hongrois
    "IDR": "IDR",      # Roupie indonésienne
    "INR": "INR",      # Roupie indienne
    "ISK": "ISK",      # Couronne islandaise
    "CNY": "CNY",      # Yuan chinois
    "DKK": "DKK",      # Couronne danoise
    "MRY": "MYR",      # Ringgit malaisien (erreur typographique dans CSV)
    "NOK": "NOK",      # Couronne norvégienne
    "SEK": "SEK",      # Couronne suédoise
    "SGD": "SGD",      # Dollar de Singapour
    "TRY": "TRY",      # Livre turque
    "HKD": "HKD",      # Dollar de Hong Kong
    "BRL": "BRL",      # Réal brésilien
    "KRW": "KRW",      # Won sud-coréen
    "MXN": "MXN"       # Peso mexicain
}


def get_conversion_rate_frankfurter(date: str, from_currency: str, to_currency: str = "EUR") -> float:

    # Conversion du symbole si nécessaire
    try:
        from_currency = currency_symbols[from_currency]
        date_iso = _to_iso_date(date)
        url = f"https://api.frankfurter.app/{date_iso}"
        params = {"from": from_currency.upper(), "to": to_currency.upper()}
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        rate = data.get("rates", {}).get(to_currency.upper())
        if rate is None:
            raise ValueError(f"Taux introuvable dans la réponse: {data}")
        return float(rate)
    except:
        return False


def clean_dataframes(df_rev, df_bo, match_libelle):

    # BO
    df_bo_clean = df_bo.copy()
    df_bo_clean["Date"] = pd.to_datetime(df_bo_clean["Date"], errors="coerce")
    # On ne garde que les lignes du compte de trésorerie (511xxx)
    df_bo_clean = df_bo_clean[df_bo_clean["Compte"].astype(str).str.startswith("511")]
    # Valeur absolue pour gérer les lignes avec Débit/Crédit inversés
    df_bo_clean["Montant"] = (df_bo_clean["Débit(€)"] - df_bo_clean["Crédit (€)"]).abs()
    df_bo_clean = df_bo_clean[df_bo_clean["Montant"] > 0]
    df_bo_clean = df_bo_clean.reset_index().rename(columns={"index": "idx_bo"})

    # ✅ Sécurité : la colonne d'identifiant BO existe toujours et est normalisée
    if "BoTxnId" not in df_bo_clean.columns:
        df_bo_clean["BoTxnId"] = ""
    df_bo_clean["BoTxnId"] = df_bo_clean["BoTxnId"].map(_norm_txn_id)

    # Revolut
    df_rev_clean = df_rev.copy()
    # On inclut CARD_PAYMENT et REFUND
    df_rev_clean = df_rev_clean[df_rev_clean["Type"].isin(["CARD_PAYMENT", "REFUND", "CARD_REFUND"])]
    df_rev_clean["Date"] = pd.to_datetime(df_rev_clean["Date started (UTC)"], errors="coerce")
    # Valeur absolue pour gérer les remboursements (Total amount positif)
    df_rev_clean["Montant"] = pd.to_numeric(df_rev_clean["Total amount"], errors="coerce").abs()

    cols_rev_keep = [
        "Date", "Montant", "Description", "ID", "Type", "State",
        "Card number", "Card label", "Payer", "Exchange rate",
        "Orig currency", "Orig amount", "email","email_binome", "RevTxnId"
    ]

    df_rev_clean = df_rev_clean[[c for c in cols_rev_keep if c in df_rev_clean.columns]]
    df_rev_clean["Libelle_match"] = df_rev_clean["Description"].map(match_libelle)
    df_rev_clean = df_rev_clean.reset_index().rename(columns={"index": "idx_rev"})

    # ✅ Sécurité : la colonne d'identifiant Revolut existe toujours et est normalisée
    if "RevTxnId" not in df_rev_clean.columns:
        df_rev_clean["RevTxnId"] = ""
    df_rev_clean["RevTxnId"] = df_rev_clean["RevTxnId"].map(_norm_txn_id)

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


def _to_iso_date(v) -> str | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    try:
        return pd.to_datetime(s, dayfirst=True, errors="raise").strftime("%Y-%m-%d")
    except Exception:
        try:
            return pd.to_datetime(float(s), unit="D", origin="1899-12-30").strftime("%Y-%m-%d")
        except Exception:
            return None


def crm_login():
    # cache simple en session pour éviter de relog à chaque ligne
    if "crm_token" in st.session_state and "crm_hash" in st.session_state:
        return st.session_state["crm_hash"], st.session_state["crm_token"]

    BASE_URL = st.secrets["crm"]["base_url"].rstrip("/")
    AUTH_URL = f"{BASE_URL}/api/appMember/concierge/login"

    EMAIL = st.secrets["crm"]["email"]
    PASSWORD = st.secrets["crm"]["password"]

    auth_resp = requests.post(AUTH_URL, json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    auth_resp.raise_for_status()

    auth_data = auth_resp.json()
    if not auth_data.get("success"):
        raise RuntimeError(f"Login failed: {auth_data}")

    ConciergeHash = str(auth_data.get("ConciergeHash", "")).strip()
    ApiToken = str(auth_data.get("ApiToken", "")).strip()
    if not ConciergeHash or not ApiToken:
        raise RuntimeError("Missing ConciergeHash or ApiToken in login response.")

    st.session_state["crm_hash"] = ConciergeHash
    st.session_state["crm_token"] = ApiToken
    return ConciergeHash, ApiToken


def crm_update_amount(invoice_number: str, new_amount: float, date_iso: str):
    BASE_URL = st.secrets["crm"]["base_url"].rstrip("/")
    AMOUNT_URL_TMPL = f"{BASE_URL}/api/myagency/controller/amount/{{ConciergeHash}}"

    ConciergeHash, ApiToken = crm_login()
    url = AMOUNT_URL_TMPL.format(ConciergeHash=ConciergeHash)

    payload = {
        "payload": {
            "type": "expense",
            "invoiceNumber": str(invoice_number).strip(),
            "date": date_iso,
            "amount": float(new_amount),
        }
    }

    headers = {"Content-Type": "application/json", "ApiToken": ApiToken}
    resp = requests.post(url, json=payload, headers=headers, timeout=15)

    ctype = (resp.headers.get("content-type") or "").lower()
    body = resp.json() if "application/json" in ctype else resp.text
    return resp.status_code, body


def crm_update_date(invoice_number: str, current_date_iso: str, new_date_iso: str):
    BASE_URL = st.secrets["crm"]["base_url"].rstrip("/")
    DATE_URL_TMPL = f"{BASE_URL}/api/myagency/controller/date/{{ConciergeHash}}"

    ConciergeHash, ApiToken = crm_login()
    url = DATE_URL_TMPL.format(ConciergeHash=ConciergeHash)

    payload = {
        "payload": {
            "type": "expense",  # "expense" || "partner"
            "invoiceNumber": str(invoice_number).strip(),
            "currentDate": current_date_iso,
            "newDate": new_date_iso
        }
    }

    headers = {
        "Content-Type": "application/json",
        "ApiToken": ApiToken
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=15)

    try:
        body = resp.json()
    except:
        body = {"raw": resp.text}

    return resp.status_code, body



def run_api_crm(num_de_piece,value,date):
    BASE_URL = st.secrets["crm"]["base_url"]
    AUTH_URL = f"{BASE_URL}/api/appMember/concierge/login"
    ACCOUNTING_URL_TMPL = f"{BASE_URL}/api/myagency/controller/accounting/{{ConciergeHash}}"

    EMAIL = st.secrets["crm"]["email"]
    PASSWORD =st.secrets["crm"]["password"]
    if not PASSWORD:
        raise RuntimeError("Missing CRM_PASSWORD. …")

    auth_payload = {"email": EMAIL, "password": PASSWORD}
    auth_resp = requests.post(AUTH_URL, json=auth_payload, timeout=30)
    auth_resp.raise_for_status()

    auth_ct = (auth_resp.headers.get("content-type") or "").lower()
    auth_data = auth_resp.json() if "application/json" in auth_ct else {}
    if not auth_data.get("success"):
        raise RuntimeError(f"Login failed: {auth_data}")

    ConciergeHash = str(auth_data.get("ConciergeHash", "")).strip()
    ApiToken = str(auth_data.get("ApiToken", "")).strip()
    if not ConciergeHash or not ApiToken:
        raise RuntimeError("Missing ConciergeHash or ApiToken in login response.")


    url = ACCOUNTING_URL_TMPL.format(ConciergeHash=ConciergeHash)

    payload = {
        "payload": {
            "InvoiceNumber": num_de_piece,
            "type": "partner",
            "field": "achat",
            "value": value,
            "date":date
        }
    }

    headers = {
        "Content-Type": "application/json",
        "ApiToken": ApiToken,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        ctype = (resp.headers.get("content-type") or "").lower()
        json_body = resp.json() if "application/json" in ctype else {}

        return {
            "status": resp.status_code,
            "type": "Réponse JSON",
            "body": json_body,
            "message": json_body.get("message", "Aucun message"),
            "success": json_body.get("success", False),
        }

    except Exception as e:
        return {
            "status": resp.status_code if 'resp' in locals() else 500,
            "type": "Réponse brute ou erreur",
            "body": resp.text if 'resp' in locals() else str(e),
            "message": "Erreur de traitement ou JSON invalide",
            "success": False,
        }


def save_carryover_to_sheet(df):
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
        sh = gc.open(sheet_name)

        try:
            ws = sh.worksheet("Carryover")
        except:
            ws = sh.add_worksheet(title="Carryover", rows=500, cols=20)

        df_new = df.copy()
        for col in df_new.select_dtypes(include=["datetime64[ns]", "datetimetz"]).columns:
            df_new[col] = df_new[col].dt.strftime("%Y-%m-%d")
        df_new = df_new.fillna("").astype(str)

        # ✅ Clé composite stable : Libelle + Date + Montant
        def make_dedup_key(df):
            return (
                df["Libelle"].str.strip()
                + "|"
                + df["Date"].astype(str).str.strip()
                + "|"
                + df["Montant"].astype(str).str.strip()
            )

        existing_data = ws.get_all_records()

        if existing_data:
            df_existing = pd.DataFrame(existing_data).astype(str)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined["_dedup_key"] = make_dedup_key(df_combined)
            df_combined = df_combined.drop_duplicates(subset=["_dedup_key"], keep="last")
            df_combined = df_combined.drop(columns=["_dedup_key"])
        else:
            df_combined = df_new

        ws.clear()
        ws.update([df_combined.columns.tolist()] + df_combined.values.tolist())
        return True

    except Exception as e:
        st.error(f"Erreur sauvegarde Carryover : {e}")
        return False

def load_carryover_from_sheet():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
        sh = gc.open(sheet_name)
        ws = sh.worksheet("Carryover")
        data = ws.get_all_records()
        if data:
            df = pd.DataFrame(data)
            if "Date" in df.columns:
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
            if "Montant" in df.columns:
                df["Montant"] = pd.to_numeric(df["Montant"], errors="coerce")
            return df
    except:
        pass
    return pd.DataFrame()


def clear_carryover_from_sheet():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        gc = gspread.service_account_from_dict(creds_dict)
        sh = gc.open(sheet_name)
        ws = sh.worksheet("Carryover")
        ws.clear()
        return True
    except Exception as e:
        st.error(f"Erreur suppression Carryover : {e}")
        return False
# @st.cache_data(show_spinner=False, ttl=1800)


# ============================================================
# 3. Logique Principale
# ============================================================

def run_interface():

    # st.write("✅ depenses.py version 2025-12-04 14h - DEBUG")

    # --- Style & en-tête de la page (rendu ici, pas au niveau global) ---
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    st.title("💳 Rapprochement Bancaire Intelligent")
    st.markdown("---")

    # --- Récupération Sécurisée de la Clé API OpenAI ---
    try:
        API_KEY = st.secrets["crm"]["api_key"]
    except Exception:
        st.error("❌ Erreur : Impossible de récupérer la clé API OpenAI. Vérifiez [crm] api_key dans secrets.toml.")
        st.stop()

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

    # 1. Chargement des données brutes (refait à chaque rerun, c'est OK)
    df_rev_raw, df_bo_raw = load_data(uploaded_revolut, uploaded_bo)


    # =========================
    # Vérification des "???" dans la colonne Compte
    # =========================

    df_compte_missing = pd.DataFrame()

    if "Compte" in df_bo_raw.columns:
        df_compte_missing = df_bo_raw[df_bo_raw["Compte"] == "???"].copy()
        df_compte_missing = df_compte_missing.reset_index()
        df_compte_missing = df_compte_missing.drop_duplicates(subset="Libelle").copy()


    # =========================
    # Vérification & correction des comptes (affichage persistant)
    # =========================
    if "compte_verified" not in st.session_state:
        st.session_state["compte_verified"] = False

    if "api_row" not in st.session_state:
        st.session_state["api_row"] = []

    if "crm_logs" not in st.session_state:
        st.session_state["crm_logs"] = []

    if "crm_api_logs_ko" not in st.session_state:
        st.session_state["crm_api_logs_ko"] = []

    if "df_bo_raw" not in st.session_state:
        st.session_state["df_bo_raw"] = df_bo_raw
    else:
        df_bo_raw = st.session_state["df_bo_raw"]

    if len(df_compte_missing) > 0:

        st.subheader("🧾 Correction des comptes BackOffice")

        disabled_mode = st.session_state["compte_verified"]

        edited_compte = st.data_editor(
            df_compte_missing[["index", "Date", "Libelle", "Débit(€)", "Crédit (€)", "Compte","NumCompta"]],
            column_config={
                "index": None,
                "Date": st.column_config.TextColumn("Date", disabled=True),
                "Libelle": st.column_config.TextColumn("Libellé", disabled=True),
                "Débit(€)": st.column_config.NumberColumn("Débit (€)", format="%.2f €", disabled=True),
                "Crédit (€)": st.column_config.NumberColumn("Crédit (€)", format="%.2f €", disabled=True),
                "Compte": st.column_config.TextColumn(
                    "Compte",
                    disabled=disabled_mode,
                    help="Compte comptable BackOffice"
                ),
                "NumCompta": st.column_config.TextColumn("NumCompta", disabled=True),
            },
            use_container_width=True,
            hide_index=True,
            key="compte_editor"
        )

        if st.session_state["api_row"]:
            with st.expander("Détails des mises à jour CRM"):
                    for line in st.session_state["api_row"]:
                        st.write(line)

        if not st.session_state["compte_verified"]:
            if st.button("✅ Continuer vers le mapping IA"):
                api_logs = []
                for _, row in edited_compte.iterrows():
                    if str(row["Compte"]) != "???":
                        idx = df_bo_raw[
                            (df_bo_raw["Libelle"] == row["Libelle"]) &
                            (~df_bo_raw["Compte"].astype(str).str.startswith("511"))
                        ].index

                        if not idx.empty:
                            df_bo_raw.loc[idx, "Compte"] = row["Compte"]

                        with st.spinner("Mise à jour des comptes tiers dans le CRM (seulement les lignes modifiées)…"):
                            invoice_number = str(row["NumCompta"]).strip()
                            compte_value = str(row["Compte"]).strip()
                            date = _to_iso_date(str(row["Date"]).strip())

                            # skip si facture vide
                            if not invoice_number:
                                api_logs.append(f"⚠️ Facture sans numéro de piece — ligne ignorée.")
                                continue

                            result = run_api_crm(invoice_number, compte_value, date)
                            if result["status"] and 200 <= result["status"]  < 300:
                                if result["success"]==False:
                                    if result["message"]=="Line not updated, same value":
                                        api_logs.append(f"❌ CRM ko — numéro de piece {invoice_number} → {compte_value} (HTTP {result['status'] }) | Compte Tiers identique sur CRM donc pas de mise a jour")
                                    else :
                                        api_logs.append(f"❌ CRM ko — numéro de piece {invoice_number} → {compte_value} (HTTP {result['status'] }) | Numero de piece non existant")
                                else:
                                    api_logs.append(f"✅ CRM ok — numéro de piece {invoice_number} → {compte_value} (HTTP {result['status'] },hey {result['success']},{result['message']})")
                            else:
                                api_logs.append(f"❌ CRM ko — numéro de piece {invoice_number} → {compte_value} (HTTP {result['status'] }) | {result['body'] }")


                st.session_state["api_row"] = api_logs

                st.session_state["df_bo_raw"] = df_bo_raw
                st.session_state["compte_verified"] = True
                st.success("✅ Vérification Compte OK. Lancement de l'analyse...")
                st.rerun()

        else:
            st.info("🔒 Comptes validés — affichage en lecture seule")

                # ✅ AJOUT ICI : bloque tant que pas validé
        if len(df_compte_missing) > 0 and not st.session_state["compte_verified"]:
            st.stop()

        # Si pas de "???" ou colonne Compte inexistante, passer directement
        st.session_state["compte_verified"] = True

    st.success("✅ Vérification Compte OK. Lancement de l'analyse...")
    st.markdown("---")

    # st.dataframe(st.session_state["df_bo_raw"])

    if "Libelle" not in df_bo_raw.columns:
        st.error("Colonne 'Libelle' introuvable dans le fichier BackOffice.")
        st.stop()

    # ============================================================
    # ⚡ L'analyse IA ne sert qu'au matching CLASSIQUE (fallback),
    #    donc uniquement aux lignes SANS identifiant de transaction.
    #    Les lignes avec dTransactionId != 0 sont déjà matchées par l'ID
    #    -> on les exclut du mapping IA (gain de tokens + zéro faux mapping).
    # ============================================================

    # Ensemble des identifiants de transaction présents côté BO (dTransactionId != 0)
    if "BoTxnId" in df_bo_raw.columns:
        bo_txn_ids = set(df_bo_raw.loc[df_bo_raw["BoTxnId"] != "", "BoTxnId"].dropna().unique())
        # Libellés BO uniquement pour les lignes SANS ID (dTransactionId = 0)
        df_bo_for_ai = df_bo_raw[df_bo_raw["BoTxnId"] == ""]
    else:
        bo_txn_ids = set()
        df_bo_for_ai = df_bo_raw

    backoffice_labels = sorted(df_bo_for_ai["Libelle"].dropna().unique().tolist())

    # Côté Revolut : on garde les descriptions des lignes qui NE matcheront PAS par ID
    # (pas d'ID, ou ID absent du BO).
    if "RevTxnId" in df_rev_raw.columns:
        rev_no_id_match = df_rev_raw[
            (df_rev_raw["RevTxnId"] == "") | (~df_rev_raw["RevTxnId"].isin(bo_txn_ids))
        ]
    else:
        rev_no_id_match = df_rev_raw

    revolut_labels = sorted(rev_no_id_match["Description"].dropna().unique().tolist())

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
                # Premier passage : on appelle l'IA
                match_libelle = get_ai_mapping(API_KEY, revolut_labels, backoffice_labels)
                st.session_state["match_libelle"] = match_libelle
            else:
                # Rerun : on réutilise le mapping déjà obtenu
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
        df_mapping = df_mapping[df_mapping["Libelle BO"] != "match non trouvé"]

        edited_mapping = st.data_editor(
            df_mapping,
            column_config={
                "Valide": st.column_config.CheckboxColumn("Accepter ?", default=True),
                "Libelle Revolut": st.column_config.TextColumn("Libellé Revolut", disabled=True),
                "Libelle BO": st.column_config.TextColumn("Libellé BO", disabled=True),
            },
            use_container_width=True,
            hide_index=True,
            key="mapping_editor"
        )

        # Bouton de validation du mapping
        if st.button("✅ Valider le mapping et Lancer le Rapprochement"):
            # 1. On récupère le mapping actuel (celui retourné par l'IA ou déjà en session)
            current_map = st.session_state.get("match_libelle", {}) or match_libelle or {}

            # 2. On crée une copie que l'on va mettre à jour
            updated_map = current_map.copy()

            # 3. Pour chaque ligne décochée, on force "match non trouvé"
            for index, row in edited_mapping.iterrows():
                if row["Valide"] is False:
                    updated_map[row["Libelle Revolut"]] = "match non trouvé"

            # 4. On sauvegarde le mapping corrigé en session
            st.session_state["match_libelle"] = updated_map

            # 5. On prépare les dataframes clean et on les met en session
            df_rev_clean, df_bo_clean = clean_dataframes(df_rev_raw, df_bo_raw, updated_map)
            st.session_state["df_rev_clean"] = df_rev_clean
            st.session_state["df_bo_clean"] = df_bo_clean

            # 6. On passe en phase dashboard et on rerun
            st.session_state["phase"] = "dashboard"
            st.rerun()

        # Tant qu'on n'a pas validé le mapping, on ne va pas plus loin
        return

    # ============================================================
    # PHASE 2 : DASHBOARD & MATCHING (phase == "dashboard")
    # ============================================================

    # On récupère les objets depuis le state
    match_libelle = st.session_state["match_libelle"]
    df_rev_clean = st.session_state["df_rev_clean"]
    df_bo_clean = st.session_state["df_bo_clean"]

    # Sécurité : si pour une raison X les df ne sont pas en state, on les recalcule
    if df_rev_clean is None or df_bo_clean is None:
        df_rev_clean, df_bo_clean = clean_dataframes(df_rev_raw, df_bo_raw, match_libelle)
        st.session_state["df_rev_clean"] = df_rev_clean
        st.session_state["df_bo_clean"] = df_bo_clean

    # Chargement du carryover depuis Google Sheets
    if "carryover_injected" not in st.session_state:
        df_carry = load_carryover_from_sheet()
        if not df_carry.empty and "Payer" in df_carry.columns:
            # On garde uniquement les Payer présents dans le Revolut chargé
            payers_revolut_courant = set(df_rev_clean["Payer"].dropna().unique())
            df_carry_filtre = df_carry[df_carry["Payer"].isin(payers_revolut_courant)].copy()

            if not df_carry_filtre.empty:
                st.info(f"♻️ {len(df_carry_filtre)} dépenses BO du mois précédent réinjectées pour : {', '.join(payers_revolut_courant)}")
                max_idx = df_bo_clean["idx_bo"].max() + 1
                df_carry_filtre["idx_bo"] = range(int(max_idx), int(max_idx) + len(df_carry_filtre))
                df_bo_clean = pd.concat([df_bo_clean, df_carry_filtre], ignore_index=True)
                st.session_state["df_bo_clean"] = df_bo_clean
                st.session_state["bo_carryover"] = df_carry_filtre

        st.session_state["carryover_injected"] = True

    # ✅ Sécurité : après une éventuelle injection du carryover, on normalise à nouveau
    # les identifiants (les lignes du carryover n'ont pas de BoTxnId -> "").
    if "BoTxnId" not in df_bo_clean.columns:
        df_bo_clean["BoTxnId"] = ""
    df_bo_clean["BoTxnId"] = df_bo_clean["BoTxnId"].map(_norm_txn_id)
    if "RevTxnId" not in df_rev_clean.columns:
        df_rev_clean["RevTxnId"] = ""
    df_rev_clean["RevTxnId"] = df_rev_clean["RevTxnId"].map(_norm_txn_id)

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

    # ✅ INSERTION ICI (juste après maj_sets)
    if "no_invoice_final" not in st.session_state:
        st.session_state["no_invoice_final"] = pd.DataFrame()

    if "no_invoice_ids_rev" not in st.session_state:
        st.session_state["no_invoice_ids_rev"] = set()

    if "no_invoice_ids_bo" not in st.session_state:
        st.session_state["no_invoice_ids_bo"] = set()

    # ============================================================
    # 0) MATCH PRIORITAIRE PAR IDENTIFIANT DE TRANSACTION
    #    colonne C Revolut (RevTxnId) == colonne M / dTransactionId BO (BoTxnId)
    #    -> uniquement pour les lignes BO où dTransactionId != 0
    # ============================================================
    matches_id = (
        df_rev_clean[df_rev_clean["RevTxnId"] != ""]
        .merge(
            df_bo_clean[df_bo_clean["BoTxnId"] != ""],
            left_on="RevTxnId",
            right_on="BoTxnId",
            how="inner",
            suffixes=("_rev", "_bo"),
        )
        .drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_id)

    # ✅ Pour le matching "classique", on ne travaille QUE sur :
    #    - les lignes Revolut pas encore matchées par l'ID
    #    - les lignes BO SANS identifiant (dTransactionId = 0)  -> "faire le match actuel"
    df_rev_f = df_rev_clean[~df_rev_clean["idx_rev"].isin(used_rev)]
    df_bo_f = df_bo_clean[
        (df_bo_clean["BoTxnId"] == "")
        & (~df_bo_clean["idx_bo"].isin(used_bo))
    ]

    # -- Algorithmes (sur le sous-ensemble "sans ID") --
    matches_ok = filtre_nouveaux(
        df_rev_f.merge(
            df_bo_f,
            left_on=["Date", "Montant", "Libelle_match"],
            right_on=["Date", "Montant", "Libelle"],
            how="inner",
            suffixes=("_rev", "_bo")
        )
        .drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_ok)

    matches_sans_libelle = filtre_nouveaux(
        df_rev_f.merge(
            df_bo_f,
            left_on=["Date", "Montant"],
            right_on=["Date", "Montant"],
            how="inner",
            suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_sans_libelle)

    m_sans_conversion = (
    df_rev_f.merge(
        df_bo_f,
        left_on=["Libelle_match"],
        right_on=["Libelle"],
        how="inner",
        suffixes=("_rev", "_bo")
    )
    .drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    m_sans_conversion["ecart_jours"] = (m_sans_conversion["Date_bo"] - m_sans_conversion["Date_rev"]).dt.days.abs()
    matches_potentiel_sans_conversion = filtre_nouveaux(m_sans_conversion[m_sans_conversion["ecart_jours"] <= 3])
    matches_potentiel_sans_conversion=matches_potentiel_sans_conversion[matches_potentiel_sans_conversion['Orig currency']!='EUR']
    matches_potentiel_sans_conversion=matches_potentiel_sans_conversion[matches_potentiel_sans_conversion['Exchange rate'].isna()]

    maj_sets(matches_potentiel_sans_conversion)

    matches_sans_date = filtre_nouveaux(
        df_rev_f.merge(
            df_bo_f,
            left_on=["Montant", "Libelle_match"],
            right_on=["Montant", "Libelle"],
            how="inner",
            suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_sans_date)

    matches_sans_montant = filtre_nouveaux(
        df_rev_f.merge(
            df_bo_f,
            left_on=["Date", "Libelle_match"],
            right_on=["Date", "Libelle"],
            how="inner",
            suffixes=("_rev", "_bo")
        ).drop_duplicates(subset=["idx_rev", "idx_bo"])
    )
    maj_sets(matches_sans_montant)

    m_pot = (
        df_rev_f.merge(
            df_bo_f,
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


    # Enlève des matches les lignes déjà classées "OK sans facture"
    if st.session_state.get("no_invoice_ids_rev") or st.session_state.get("no_invoice_ids_bo"):
        bad_rev = st.session_state.get("no_invoice_ids_rev", set())
        bad_bo  = st.session_state.get("no_invoice_ids_bo", set())

        def _rm_noinv(df):
            if df is None or df.empty:
                return df
            if "idx_rev" in df.columns:
                df = df[~df["idx_rev"].isin(bad_rev)]
            if "idx_bo" in df.columns:
                df = df[~df["idx_bo"].isin(bad_bo)]
            return df

        matches_id = _rm_noinv(matches_id)
        matches_ok = _rm_noinv(matches_ok)
        matches_sans_libelle = _rm_noinv(matches_sans_libelle)
        matches_sans_date = _rm_noinv(matches_sans_date)
        matches_potentiel_sans_conversion = _rm_noinv(matches_potentiel_sans_conversion)
        matches_sans_montant = _rm_noinv(matches_sans_montant)
        matches_potentiel = _rm_noinv(matches_potentiel)

    # ✅ KO initiaux (DOIT ÊTRE TOUJOURS DÉFINI, DONC HORS DU IF)
    matches_ko_rev_initial = df_rev_clean[~df_rev_clean["idx_rev"].isin(used_rev)]
    matches_ko_bo_initial = df_bo_clean[~df_bo_clean["idx_bo"].isin(used_bo)]


    if "ko_rev_final" not in st.session_state:
        st.session_state["ko_rev_final"] = matches_ko_rev_initial
    if "ko_bo_final" not in st.session_state:
        st.session_state["ko_bo_final"] = matches_ko_bo_initial

    if len(st.session_state["ko_rev_final"]) == 0 and len(matches_ko_rev_initial) > 0:
        st.session_state["ko_rev_final"] = matches_ko_rev_initial
        st.session_state["ko_bo_final"] = matches_ko_bo_initial

    # KPIs + tabs (tu peux garder ton code existant ici)
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    total_rev = len(df_rev_clean)
    total_matched = len(used_rev)
    percent = round((total_matched / total_rev) * 100, 1) if total_rev > 0 else 0

    col1.metric("Total Transactions", total_rev)
    col2.metric("Matchées (Init)", total_matched, f"{percent}%")
    col3.metric("Match par ID", len(matches_id))
    col4.metric("KO Revolut (Actuel)", len(st.session_state["ko_rev_final"]), delta_color="inverse")
    col5.metric("KO BackOffice (Actuel)", len(st.session_state["ko_bo_final"]), delta_color="inverse")
    col6.metric("OK sans facture", len(st.session_state["no_invoice_final"]))


    # st.markdown("## 📡 Journal des mises à jour CRM")

    # if len(st.session_state["crm_logs"]) == 0:
    #     st.info("Aucune mise à jour CRM effectuée pour l’instantt.")
    # else:
    #     log_df = pd.DataFrame(st.session_state["crm_logs"])
    #     st.dataframe(
    #         log_df.sort_values("time", ascending=False),
    #         use_container_width=True,
    #         hide_index=True
    #     )

    #     if st.button("🧹 Effacer les logs CRM"):
    #         st.session_state["crm_logs"] = []
    #         st.rerun()



    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "✅ Matches & Validation",
    "⚠️ KO Revolut (À traiter)",
    "⚠️ KO BackOffice",
    "🧾 Dépenses OK sans facture",
    "📤 Relances des dépenses",
    "📦 Export vers Sage",
    "🗂️ Mémoire inter-mois"
])




    # ... et là tu remets ton bloc tab1 / tab2 / tab3 / tab4 tel que tu l'avais

    # --- TAB 1 : Tableaux Interactifs --- (tu peux garder strictement ton code actuel)
    # (reprends ici ton bloc tab1 / tab2 / tab3 / tab4 inchangé)

    # --- TAB 1 : Tableaux Interactifs ---


    with tab1:
        st.info(
            "Décochez la case 'Valide ?' si un rapprochement est incorrect, "
            "puis cliquez sur 'Mettre à jour' en bas de page."
        )

        # Colonnes de base
        base_cols = [
            "idx_rev", "idx_bo", "Date", "Montant",
            "Description", "Libelle","Invoice","ExperienceDate", "Payer",
            "Exchange rate", "Orig currency", "Orig amount",
            "email","email_binome","Compte","NumCompta", "RevTxnId", "BoTxnId"
        ]

        safe_cols = lambda df: [c for c in base_cols if c in df.columns]

        with st.expander(
            f"🆔 Matchs par identifiant de transaction (colonne C Revolut = dTransactionId BO) ({len(matches_id)})",
            expanded=True
        ):
            st.success(
                "Ces lignes ont été rapprochées **directement via l'identifiant de transaction** "
                "(colonne C du CSV Revolut = colonne `dTransactionId` du BackOffice). "
                "C'est le rapprochement le plus fiable.\n\n"
                "👉 Statut **« ✅ Valide + modif CRM »** : la **date** et le **montant** du Back Office "
                "seront automatiquement alignés sur ceux de **Revolut**. "
                "Choisissez **« 🔒 Valide sans modif CRM »** pour valider sans rien modifier, "
                "ou **« ❌ KO »** pour rejeter la ligne."
            )
            cols_id = [
                "idx_rev", "idx_bo",
                "RevTxnId", "BoTxnId",
                "Date_rev", "Date_bo",
                "Montant_rev", "Montant_bo",
                "Description", "Libelle", "Invoice", "ExperienceDate", "Payer",
                "Exchange rate", "Orig currency", "Orig amount",
                "email", "email_binome", "Compte", "NumCompta"
            ]
            df_id_view = matches_id[[c for c in cols_id if c in matches_id.columns]].copy()
            df_id_view.insert(0, "Statut", "✅ Valide + modif CRM")

            edited_id = st.data_editor(
                df_id_view,
                column_config={
                    "Statut": st.column_config.SelectboxColumn(
                        "Statut",
                        options=["✅ Valide + modif CRM", "🔒 Valide sans modif CRM", "❌ KO"],
                        required=True,
                    ),
                    "idx_rev": None,
                    "idx_bo": None,
                    "Date_rev": st.column_config.DateColumn("Date Revolut", format="DD/MM/YYYY"),
                    "Date_bo": st.column_config.DateColumn("Date BO", format="DD/MM/YYYY"),
                    "Montant_rev": st.column_config.NumberColumn("Montant Rev", format="%.2f €"),
                    "Montant_bo": st.column_config.NumberColumn("Montant BO", format="%.2f €"),
                },
                use_container_width=True,
                hide_index=True,
                key="editor_id",
                disabled=[c for c in df_id_view.columns if c != "Statut"]
            )

        with st.expander(
            f"✅ Matchs parfaits : même montant, même libellé et même date entre Revolut et le BO ({len(matches_ok)})",
            expanded=True
        ):
            st.success(
                "Ces lignes correspondent **exactement** entre Revolut et le Back Office : "
                "**même montant, même libellé, même date**. "
                "Sauf cas particulier, vous pouvez laisser ces rapprochements **validés**."
            )
            df_ok_view = matches_ok[safe_cols(matches_ok)]
            edited_ok = display_interactive_table(df_ok_view, "ok")


        with st.expander(
            f"🔎 Même montant & même date, libellés à vérifier ({len(matches_sans_libelle)})"
        ):
            st.warning(
                "Pour ces lignes, **le montant et la date sont identiques** entre Revolut et le Back Office, "
                "mais le libellé peut différer. "
                "👉 Vérifiez que les libellés correspondent bien avant de laisser le rapprochement **validé**."
            )
            df_sl_view = matches_sans_libelle[safe_cols(matches_sans_libelle)]
            edited_sl = display_interactive_table(df_sl_view, "sl")


        with st.expander(
            f"📅 Même montant & même libellé, date à confirmer ({len(matches_sans_date)})"
        ):
            st.warning(
                "Ici, **le montant et le libellé sont identiques** entre Revolut et le Back Office, "
                "mais la date peut diverger. "
                "👉 Vérifiez la cohérence de la date : si vous laissez le rapprochement **validé**, "
                "**la date de l’écriture sera automatiquement modifiée dans le Back Office**."
            )
            cols_sd = [
                "idx_rev", "idx_bo",
                "Date_rev", "Date_bo",
                "Montant", "Description", "Libelle","Invoice","ExperienceDate", "Payer",
                "Exchange rate", "Orig currency", "Orig amount",
                "email", "email_binome","NumCompta"
            ]
            df_sd_view = matches_sans_date[[c for c in cols_sd if c in matches_sans_date.columns]]
            edited_sd = display_interactive_table(df_sd_view, "sd")


        with st.expander(
            f"💱 Paiement en devise : même libellé & date proche, montant à contrôler ({len(matches_potentiel_sans_conversion)})"
        ):
            st.warning(
                "Ces lignes concernent des paiements faits **dans une devise étrangère** : "
                "le **libellé est identique** et la **date est proche (± 3 jours)** entre Revolut et le Back Office. "
                "👉 Vérifiez que le montant en euros dans le BO est cohérent avec la devise d’origine et le taux de change. "
                "Si vous laissez le rapprochement **validé**, **la date sera automatiquement mise à jour dans le Back Office**."
            )
            cols_pots_sans_conversion = [
                "idx_rev", "idx_bo",
                "Date_rev", "Date_bo",
                "Montant_rev", "Montant_bo", "Description", "Libelle","Invoice","ExperienceDate", "Payer",
                "Exchange rate", "Orig currency", "Orig amount",
                "email", "email_binome","NumCompta"
            ]
            df_cols_pots_sans_conversion_view = matches_potentiel_sans_conversion[
                [c for c in cols_pots_sans_conversion if c in matches_potentiel_sans_conversion.columns]
            ]
            edited_pot_sans_conversion = display_interactive_table(
                df_cols_pots_sans_conversion_view, "pot_sans_conversion"
            )

        with st.expander(
            f"💶 Même libellé & même date, montant à valider ({len(matches_sans_montant)})"
        ):
            st.warning(
                "Pour ces lignes, **le libellé et la date sont identiques** entre Revolut et le Back Office, "
                "mais le montant diffère ou doit être confirmé. "
                "👉 Vérifiez le montant : si vous laissez le rapprochement **validé**, "
                "**le montant sera automatiquement modifié dans le Back Office**."
            )
            cols_sm = [
                "idx_rev", "idx_bo",
                "Date", "Montant_rev", "Montant_bo",
                "Description", "Libelle", "Invoice", "ExperienceDate", "Payer",
                "Exchange rate", "Orig currency", "Orig amount",
                "email", "email_binome", "NumCompta"
            ]
            df_sm_view = matches_sans_montant[[c for c in cols_sm if c in matches_sans_montant.columns]].copy()
            df_sm_view.insert(0, "Statut", "✅ Valide + modif CRM")

            edited_sm = st.data_editor(
                df_sm_view,
                column_config={
                    "Statut": st.column_config.SelectboxColumn(
                        "Statut",
                        options=["✅ Valide + modif CRM", "🔒 Valide sans modif CRM", "❌ KO"],
                        required=True,
                    ),
                    "idx_rev": None,
                    "idx_bo": None,
                    "Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
                    "Montant_rev": st.column_config.NumberColumn("Montant Rev", format="%.2f €"),
                    "Montant_bo": st.column_config.NumberColumn("Montant BO", format="%.2f €"),
                },
                use_container_width=True,
                hide_index=True,
                key="editor_sm",
                disabled=[c for c in df_sm_view.columns if c != "Statut"]
            )

        with st.expander(
            f"🧩 Matchs potentiels : même libellé & date proche, à valider ({len(matches_potentiel)})"
        ):
            st.warning(
                "Ces rapprochements sont **probables** : le libellé est identique et la date est **proche (± 3 jours)**, "
                "mais la date et/ou le montant peuvent nécessiter une validation. "
                "👉 Vérifiez **la date et le montant** : si vous laissez le rapprochement **validé**, "
                "**la date et le montant seront automatiquement mis à jour dans le Back Office**."
            )
            cols_pot = [
                "idx_rev", "idx_bo",
                "Date_rev", "Date_bo",
                "Montant_rev", "Montant_bo",
                "Description", "Libelle", "Invoice", "ExperienceDate", "Payer",
                "Exchange rate", "Orig currency", "Orig amount",
                "email", "email_binome", "NumCompta"
            ]
            df_pot_view = matches_potentiel[[c for c in cols_pot if c in matches_potentiel.columns]].copy()
            df_pot_view.insert(0, "Statut", "✅ Valide + modif CRM")

            edited_pot = st.data_editor(
                df_pot_view,
                column_config={
                    "Statut": st.column_config.SelectboxColumn(
                        "Statut",
                        options=["✅ Valide + modif CRM", "🔒 Valide sans modif CRM", "❌ KO"],
                        required=True,
                    ),
                    "idx_rev": None,
                    "idx_bo": None,
                    "Date_rev": st.column_config.DateColumn("Date Revolut", format="DD/MM/YYYY"),
                    "Date_bo": st.column_config.DateColumn("Date BO", format="DD/MM/YYYY"),
                    "Montant_rev": st.column_config.NumberColumn("Montant Rev", format="%.2f €"),
                    "Montant_bo": st.column_config.NumberColumn("Montant BO", format="%.2f €"),
                },
                use_container_width=True,
                hide_index=True,
                key="editor_pot",
                disabled=[c for c in df_pot_view.columns if c != "Statut"]
            )

        st.markdown("---")

        # ============================================================
        # Helpers partagés (définis hors des boutons => toujours dispo)
        # ============================================================
        def _get_invoice_col(df):
            for c in ["NumCompta", "NumCompta_bo", "InvoiceNumber", "invoiceNumber"]:
                if c in df.columns:
                    return c
            return None

        def _safe_iso_date(v):
            d = _to_iso_date(v)
            return d or ""

        # ============================================================
        # BOUTON 1 : Écriture dans le Back Office (CRM)
        #   -> uniquement les lignes "✅ Valide + modif CRM" (ou cochées Valide)
        #   -> aligne dates / montants du BO sur Revolut
        #   AUCUNE écriture tant que tu ne cliques pas sur ce bouton.
        # ============================================================
        st.info(
            "✍️ **Écriture dans le Back Office.** Ce bouton applique dans le BO (CRM) les "
            "corrections de **date** et de **montant** pour les lignes en **« ✅ Valide + modif CRM »** "
            "(et les dates des lignes cochées « Valide » des tableaux date/devise). "
            "**Rien n'est modifié dans le BO tant que tu ne cliques pas ici.**"
        )

        if st.button("✍️ Faire les modifications dans le BO (dates & montants)"):
            st.session_state["crm_api_logs_ko"] = []
            api_logs = st.session_state["crm_api_logs_ko"]

            # edited_sd -> date = Date_rev (cases Valide)
            if edited_sd is not None and not edited_sd.empty and "Valide" in edited_sd.columns:
                inv_col = _get_invoice_col(edited_sd)
                if inv_col:
                    for _, r in edited_sd[edited_sd["Valide"] == True].iterrows():
                        inv = str(r.get(inv_col, "")).strip()
                        current_date = _safe_iso_date(r.get("Date_bo"))
                        new_date = _safe_iso_date(r.get("Date_rev"))
                        if inv and current_date and new_date:
                            status, body = crm_update_date(inv, current_date, new_date)
                            api_logs.append(f"📅 CRM date — {inv} | {current_date} → {new_date} (HTTP {status}) | {body}")

            # edited_pot_sans_conversion -> date = Date_rev (cases Valide)
            if edited_pot_sans_conversion is not None and not edited_pot_sans_conversion.empty and "Valide" in edited_pot_sans_conversion.columns:
                inv_col = _get_invoice_col(edited_pot_sans_conversion)
                if inv_col:
                    for _, r in edited_pot_sans_conversion[edited_pot_sans_conversion["Valide"] == True].iterrows():
                        inv = str(r.get(inv_col, "")).strip()
                        current_date = _safe_iso_date(r.get("Date_bo"))
                        new_date = _safe_iso_date(r.get("Date_rev"))
                        if inv and current_date and new_date:
                            status, body = crm_update_date(inv, current_date, new_date)
                            api_logs.append(f"📅 CRM date — {inv} | {current_date} → {new_date} (HTTP {status}) | {body}")

            # edited_sm -> montant = Montant_rev (Statut "✅ Valide + modif CRM")
            if edited_sm is not None and not edited_sm.empty and "Statut" in edited_sm.columns:
                inv_col = _get_invoice_col(edited_sm)
                if inv_col:
                    for _, r in edited_sm.iterrows():
                        if r.get("Statut") != "✅ Valide + modif CRM":
                            continue
                        inv = str(r.get(inv_col, "")).strip()
                        amt = r.get("Montant_rev")
                        date_iso = _safe_iso_date(r.get("Date") if "Date" in edited_sm.columns else r.get("Date_rev"))
                        if inv and pd.notna(amt) and date_iso:
                            status, body = crm_update_amount(inv, float(amt), date_iso)
                            api_logs.append(f"💰 CRM montant — numéro de piece {inv} → {amt} (HTTP {status}) | {body}")

            # edited_pot -> date + montant (Statut "✅ Valide + modif CRM")
            if edited_pot is not None and not edited_pot.empty and "Statut" in edited_pot.columns:
                inv_col = _get_invoice_col(edited_pot)
                if inv_col:
                    for _, r in edited_pot.iterrows():
                        if r.get("Statut") != "✅ Valide + modif CRM":
                            continue
                        inv = str(r.get(inv_col, "")).strip()
                        amt = r.get("Montant_rev")
                        current_date = _safe_iso_date(r.get("Date_bo"))
                        new_date = _safe_iso_date(r.get("Date_rev"))
                        date_iso = new_date or current_date
                        if inv and current_date and new_date:
                            status, body = crm_update_date(inv, current_date, new_date)
                            api_logs.append(f"📅 CRM date — {inv} | {current_date} → {new_date} (HTTP {status}) | {body}")
                        if inv and pd.notna(amt) and date_iso:
                            status, body = crm_update_amount(inv, float(amt), date_iso)
                            api_logs.append(f"💰 CRM montant — numéro de piece {inv} → {amt} (HTTP {status}) | {body}")

            # edited_id (match par identifiant) -> date + montant (Statut "✅ Valide + modif CRM")
            if edited_id is not None and not edited_id.empty and "Statut" in edited_id.columns:
                inv_col = _get_invoice_col(edited_id)
                for _, r in edited_id.iterrows():
                    if r.get("Statut") != "✅ Valide + modif CRM":
                        continue
                    inv = str(r.get(inv_col, "")).strip() if inv_col else ""
                    amt = r.get("Montant_rev")
                    current_date = _safe_iso_date(r.get("Date_bo"))
                    new_date = _safe_iso_date(r.get("Date_rev"))
                    if inv and current_date and new_date and current_date != new_date:
                        status, body = crm_update_date(inv, current_date, new_date)
                        api_logs.append(f"📅 CRM date (ID) — {inv} | {current_date} → {new_date} (HTTP {status}) | {body}")
                    if inv and pd.notna(amt) and new_date:
                        status, body = crm_update_amount(inv, float(amt), new_date)
                        api_logs.append(f"💰 CRM montant (ID) — numéro de piece {inv} → {amt} (HTTP {status}) | {body}")

            if not api_logs:
                api_logs.append("ℹ️ Aucune ligne « ✅ Valide + modif CRM » à envoyer au BO.")
            st.success("✅ Modifications envoyées au Back Office.")
            st.rerun()

        st.markdown("---")

        st.warning(
        """
        ⚠️ **Important**

        En décochant des lignes (ou en passant un Statut à **❌ KO**) dans les tableaux ci-dessus
        puis en cliquant sur **"Mettre à jour les KO avec les rejets"** :

        - Les rapprochements rejetés seront envoyés dans les onglets **"KO Revolut"** et **"KO BackOffice"**
        - Ces lignes serviront ensuite de **base de travail pour les corrections dans le Back Office (BO)**.
        - ⚠️ Ce bouton **n'écrit RIEN dans le BO**. Pour appliquer dates/montants, utilise le bouton **« ✍️ Faire les modifications dans le BO »** ci-dessus.
        """
    )

        if st.button("🔄 Mettre à jour les KO avec les rejets"):

            # ✅ Déclaration anticipée pour éviter UnboundLocalError
            rejected_rev_ids = []
            rejected_bo_ids = []
            noinv_parts = []
            noinv_rev_ids = []
            noinv_bo_ids = []


            def normalize_no_invoice_df(df: pd.DataFrame) -> pd.DataFrame:
                if df is None or df.empty:
                    return df

                out = df.copy()

                # Date
                if "Date" not in out.columns and "Date_rev" in out.columns:
                    out["Date"] = out["Date_rev"]

                # Montant
                if "Montant" not in out.columns and "Montant_rev" in out.columns:
                    out["Montant"] = out["Montant_rev"]

                # Colonnes métier standardisées
                cols_wanted = [
                    "Date", "Description", "Montant",
                    "ID", "Payer", "Exchange rate",
                    "Orig currency", "Orig amount",
                    "ExperienceDate",
                    "email", "email_binome",
                    "Compte", "NumCompta"
                ]

                # Colonnes techniques à conserver
                tech_cols = [c for c in ["idx_rev", "idx_bo", "Invoice"] if c in out.columns]

                # Créer les colonnes manquantes
                for c in cols_wanted:
                    if c not in out.columns:
                        out[c] = ""

                return out[cols_wanted + tech_cols]




            all_edited = [edited_ok, edited_sl, edited_sd, edited_pot_sans_conversion]
            # edited_id, edited_sm et edited_pot sont gérés séparément via leur colonne "Statut"


            # --- Tableaux à "Statut" : seuls les ❌ KO sont traités ici (rejets).
            #     Les écritures BO (date/montant) sont gérées par le bouton
            #     « ✍️ Faire les modifications dans le BO ». Ce bouton n'écrit RIEN.

            # edited_sm : rejets KO uniquement
            if edited_sm is not None and not edited_sm.empty and "Statut" in edited_sm.columns:
                ko_sm = edited_sm[edited_sm["Statut"] == "❌ KO"]
                if "idx_rev" in ko_sm.columns:
                    rejected_rev_ids.extend(ko_sm["idx_rev"].tolist())
                if "idx_bo" in ko_sm.columns:
                    rejected_bo_ids.extend(ko_sm["idx_bo"].tolist())

            # edited_pot : rejets KO uniquement
            if edited_pot is not None and not edited_pot.empty and "Statut" in edited_pot.columns:
                ko_pot = edited_pot[edited_pot["Statut"] == "❌ KO"]
                if "idx_rev" in ko_pot.columns:
                    rejected_rev_ids.extend(ko_pot["idx_rev"].tolist())
                if "idx_bo" in ko_pot.columns:
                    rejected_bo_ids.extend(ko_pot["idx_bo"].tolist())


            def _is_no_invoice(s):
                # retourne True si Invoice != "yes" (robuste aux NaN / espaces / casse)
                s = s.astype(str).str.strip().str.lower()
                return (s != "yes") & (s != "") & (s != "nan")

            for df in all_edited:
                if not df.empty and "Valide" in df.columns:
                    rejected = df[df["Valide"] == False]
                    if not rejected.empty:
                        if "idx_rev" in rejected.columns:
                            rejected_rev_ids.extend(rejected["idx_rev"].tolist())
                        if "idx_bo" in rejected.columns:
                            rejected_bo_ids.extend(rejected["idx_bo"].tolist())
                            # ✅ NO INVOICE = Valide == True mais Invoice != yes
                    if "Invoice" in df.columns:
                        accepted = df[df["Valide"] == True].copy()
                        if not accepted.empty:
                            accepted_noinv = accepted[_is_no_invoice(accepted["Invoice"])].copy()
                            if not accepted_noinv.empty:
                                accepted_noinv = normalize_no_invoice_df(accepted_noinv)
                                noinv_parts.append(accepted_noinv)

                            if "idx_rev" in accepted_noinv.columns:
                                noinv_rev_ids.extend(accepted_noinv["idx_rev"].dropna().tolist())
                            if "idx_bo" in accepted_noinv.columns:
                                noinv_bo_ids.extend(accepted_noinv["idx_bo"].dropna().tolist())



            # =========================
            # Tableau MATCH PAR ID: edited_id -> rejets KO uniquement (aucune écriture BO)
            # =========================
            if edited_id is not None and not edited_id.empty and "Statut" in edited_id.columns:
                ko_id = edited_id[edited_id["Statut"] == "❌ KO"]
                if "idx_rev" in ko_id.columns:
                    rejected_rev_ids.extend(ko_id["idx_rev"].tolist())
                if "idx_bo" in ko_id.columns:
                    rejected_bo_ids.extend(ko_id["idx_bo"].tolist())

                # Détection "OK sans facture" sur les lignes acceptées (non KO)
                if "Invoice" in edited_id.columns:
                    accepted_id = edited_id[edited_id["Statut"] != "❌ KO"].copy()
                    if not accepted_id.empty:
                        accepted_id_noinv = accepted_id[_is_no_invoice(accepted_id["Invoice"])].copy()
                        if not accepted_id_noinv.empty:
                            accepted_id_noinv = normalize_no_invoice_df(accepted_id_noinv)
                            noinv_parts.append(accepted_id_noinv)
                            if "idx_rev" in accepted_id_noinv.columns:
                                noinv_rev_ids.extend(accepted_id_noinv["idx_rev"].dropna().tolist())
                            if "idx_bo" in accepted_id_noinv.columns:
                                noinv_bo_ids.extend(accepted_id_noinv["idx_bo"].dropna().tolist())

            rows_to_add_rev = df_rev_clean[df_rev_clean["idx_rev"].isin(rejected_rev_ids)]
            rows_to_add_bo = df_bo_clean[df_bo_clean["idx_bo"].isin(rejected_bo_ids)]

            current_ko_rev = pd.concat(
                [matches_ko_rev_initial, rows_to_add_rev]
            ).drop_duplicates(subset="idx_rev")
            current_ko_bo = pd.concat(
                [matches_ko_bo_initial, rows_to_add_bo]
            ).drop_duplicates(subset="idx_bo")

            st.session_state["ko_rev_final"] = current_ko_rev
            st.session_state["ko_bo_final"] = current_ko_bo

            # ✅ On construit / met à jour la liste "OK sans facture"
            if len(noinv_parts) > 0:
                df_noinv = pd.concat(noinv_parts, ignore_index=True)

                # dédoublonnage si possible
                if "idx_rev" in df_noinv.columns and "idx_bo" in df_noinv.columns:
                    df_noinv = df_noinv.drop_duplicates(subset=["idx_rev", "idx_bo"])
                else:
                    df_noinv = df_noinv.drop_duplicates()

                st.session_state["no_invoice_final"] = df_noinv
                st.session_state["no_invoice_ids_rev"] = set(noinv_rev_ids)
                st.session_state["no_invoice_ids_bo"] = set(noinv_bo_ids)
            else:
                st.session_state["no_invoice_final"] = pd.DataFrame()
                st.session_state["no_invoice_ids_rev"] = set()
                st.session_state["no_invoice_ids_bo"] = set()


            st.success(f"Mise à jour effectuée ! {len(rejected_rev_ids)} rapprochements rejetés.")
            st.rerun()

        # =========================
        # 📌 Logs CRM — écritures dans le BO
        # =========================
        if st.session_state.get("crm_api_logs_ko"):
            with st.expander(
                "📌 Logs CRM — dernières écritures dans le Back Office (bouton « ✍️ Faire les modifications dans le BO »)",
                expanded=True
            ):
                for line in st.session_state["crm_api_logs_ko"]:
                    st.write(line)

            if st.button("🧹 Effacer ces logs", key="clear_crm_api_logs_ko"):
                st.session_state["crm_api_logs_ko"] = []
                st.rerun()

    # --- TAB 2 & 3 : Affichage depuis le Session State ---
    with tab2:
        st.error("Ces transactions Revolut n'ont pas trouvé de correspondance (ou ont été rejetées).")
        df_ko_rev = st.session_state["ko_rev_final"]
        st.dataframe(df_ko_rev)

        csv_ko = df_ko_rev.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Télécharger CSV KO Revolut",
            data=csv_ko,
            file_name="revolut_ko.csv",
            mime="text/csv"
        )

    with tab3:
        st.warning("Ces écritures BackOffice sont orphelines (ou rejetées).")
        st.dataframe(st.session_state["ko_bo_final"])

    with tab4:
        st.header("🧾 Dépenses OK sans facture")

        df_noinv = st.session_state["no_invoice_final"]

        if df_noinv is None or df_noinv.empty:
            st.info("Aucune dépense 'OK' sans facture pour le moment.")
        else:
            st.warning(
                "Ces lignes sont **matchées** (la dépense existe) mais la colonne **Invoice** n’est pas à **yes**.\n\n"
                "👉 Action : **ajouter / rattacher une facture (pièce)** dans le BackOffice."
            )

            # tu peux réutiliser safe_cols si tu veux limiter l'affichage
            st.dataframe(df_noinv, use_container_width=True)

            csv_noinv = df_noinv.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Télécharger CSV — Dépenses OK sans facture",
                data=csv_noinv,
                file_name="depenses_ok_sans_facture.csv",
                mime="text/csv"
            )


    with tab5:
        st.header("📤 Relances des dépenses incomplètes (Export vers Google Sheets)")

        # Explication avant le boutonnnnn
        st.markdown(
        """
        **Important :**

        - En cliquant sur le bouton ci-dessous, vous activez l’automatisation qui enverra
          **tous les matins à 8h** un email aux concierges et a leur binomes avec les **dépenses incomplètes ou inexistantes**
          à ajouter dans le Back Office.
        - Le **suivi des relances** et des **dépenses à traiter** se trouve dans ce Google Sheet :
          👉 [Suivi des relances et dépenses incomplètes](https://docs.google.com/spreadsheets/d/1YTjkOrBecnD76QVr2COePkb_FKL3Aqx5xASiVWMnQys/edit?gid=0#gid=0)
        """
    )

        if "gcp_service_account" in st.secrets:
            if st.button("🚀 Relances des dépenses incomplètes"):
                try:
                    creds_dict = dict(st.secrets["gcp_service_account"])
                    gc = gspread.service_account_from_dict(creds_dict)
                    sh = gc.open(sheet_name)

                    try:
                        ws = sh.worksheet("Feuille 1")
                    except:
                        ws = sh.get_worksheet(0)

                    # =========================
                    # Export KO + OK sans facture (même onglet)
                    # =========================
                    df_ko = st.session_state.get("ko_rev_final", pd.DataFrame()).copy()
                    df_noinv = st.session_state.get("no_invoice_final", pd.DataFrame()).copy()

                    # Ajout colonne Type
                    if df_ko is not None and len(df_ko) > 0:
                        df_ko["Type"] = "KO dépense"
                    if df_noinv is not None and len(df_noinv) > 0:
                        df_noinv["Type"] = "OK sans facture"

                    # Colonnes export (tu peux en rajouter si besoin)
                    cols_export = [
                        "Type",
                        "Date", "Description", "Montant",
                        "ID", "Payer", "Exchange rate",
                        "Orig currency", "Orig amount",
                        "email", "email_binome","ExperienceDate","NumCompta"
                    ]

                    # Harmoniser colonnes (crée les colonnes manquantes)
                    def _ensure_cols(df, cols):
                        if df is None or df.empty:
                            return df
                        for c in cols:
                            if c not in df.columns:
                                df[c] = ""
                        return df[cols]

                    df_ko_final = _ensure_cols(df_ko, cols_export)
                    df_noinv_final = _ensure_cols(df_noinv, cols_export)

                    # Concat final
                    frames = [d for d in [df_ko_final, df_noinv_final] if d is not None and len(d) > 0]
                    df_export = pd.concat(frames, ignore_index=True) if len(frames) > 0 else pd.DataFrame(columns=cols_export)

                    # Format date si possible
                    if "Date" in df_export.columns and len(df_export) > 0:
                        try:
                            df_export["Date"] = pd.to_datetime(df_export["Date"], errors="coerce").dt.strftime("%d-%m-%Y")
                        except:
                            pass

                    if "ExperienceDate" in df_export.columns and len(df_export) > 0:
                        try:
                            df_export["ExperienceDate"] = pd.to_datetime(df_export["ExperienceDate"], errors="coerce").dt.strftime("%d-%m-%Y")
                        except:
                            pass

                    df_export = df_export.fillna("")

                    # Export vers sheet
                    ws.insert_rows(df_export.values.tolist(), row=2)

                    st.success(f"✅ {len(df_export)} lignes exportées avec succès (KO + OK sans facture).")


                except Exception as e:
                    st.error(f"Erreur export : {e}")
        else:
            st.warning("⚠️ Secrets GCP manquants.")

    # elif not uploaded_revolut:
    # st.info("Veuillez commencer par chargeeeeer le fichier Revolut ci-dessus."))

    with tab6:
        st.header("📦 Export vers Sage")

        st.info(
            "Ce fichier contient toutes les lignes Revolut formatées pour Sage. "
            "La colonne **Compte compta** est remplie uniquement pour les lignes matchées et validées."
        )

        # =========================
        # Construction du mapping idx_rev -> Compte (depuis tous les matches validés)
        # =========================

        compte_map = {}  # idx_rev -> Compte

        # Tous les tableaux avec colonne "Valide" (checkbox) — y compris le match par ID
        for df_match in [matches_id, matches_ok, matches_sans_libelle, matches_sans_date, matches_potentiel_sans_conversion]:
            if df_match is not None and not df_match.empty and "Compte" in df_match.columns and "idx_rev" in df_match.columns:
                for _, r in df_match.iterrows():
                    idx = r.get("idx_rev")
                    compte = r.get("Compte", "")
                    if pd.notna(idx) and pd.notna(compte) and str(compte).strip():
                        compte_map[idx] = str(compte).strip()

        # Tableaux avec colonne "Statut" (selectbox) — on prend ceux qui ne sont pas KO
        for df_match in [matches_sans_montant, matches_potentiel]:
            if df_match is not None and not df_match.empty and "Compte" in df_match.columns and "idx_rev" in df_match.columns:
                for _, r in df_match.iterrows():
                    idx = r.get("idx_rev")
                    compte = r.get("Compte", "")
                    statut = r.get("Statut", "✅ Valide + modif CRM")
                    if statut != "❌ KO" and pd.notna(idx) and pd.notna(compte) and str(compte).strip():
                        compte_map[idx] = str(compte).strip()

        # =========================
        # Construction du DataFrame Sage
        # =========================

        df_sage_source = df_rev_raw.copy()

        # Format date DD/MM/YYYY
        df_sage_source["_date_completed"] = pd.to_datetime(
            df_sage_source["Date completed (UTC)"], errors="coerce"
        ).dt.strftime("%d/%m/%Y")

        # idx_rev pour le mapping compte (basé sur l'index du CSV original filtré dans clean_dataframes)
        # On recrée l'index propre
        df_sage_source = df_sage_source.reset_index().rename(columns={"index": "idx_rev"})

        # Colonne 7 : Total amount négatif → positif
        def col_debit(val):
            try:
                v = float(val)
                return round(abs(v), 2) if v < 0 else ""
            except:
                return ""

        # Colonne 8 : Total amount positif → tel quel
        def col_credit(val):
            try:
                v = float(val)
                return round(v, 2) if v > 0 else ""
            except:
                return ""

        df_sage = pd.DataFrame({
            "Col1":         "BQ7",
            "Date":         df_sage_source["_date_completed"],
            "Payer":        df_sage_source["Payer"],
            "Col4":         "401000",
            "Compte":       df_sage_source["idx_rev"].map(lambda x: compte_map.get(x, "")),
            "Description":  df_sage_source["Description"],
            "Debit":        df_sage_source["Total amount"].map(col_debit),
            "Credit":       df_sage_source["Total amount"].map(col_credit),
        })

        st.dataframe(df_sage, use_container_width=True, hide_index=True)

        # =========================
        # Bouton téléchargement Excel
        # =========================

        import io
        buffer_sage = io.BytesIO()
        with pd.ExcelWriter(buffer_sage, engine="xlsxwriter") as writer:
            df_sage.to_excel(writer, index=False, header=False, sheet_name="Sage")

        st.download_button(
            label="📥 Télécharger le fichier Excel pour Sage",
            data=buffer_sage.getvalue(),
            file_name="export_sage.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    with tab7:
        st.header("🗂️ Mémoire inter-mois — KO BackOffice de fin de mois")

        st.info(
            "Certaines dépenses BackOffice de fin de mois (à partir du **25**) n'ont pas trouvé de match "
            "car la dépense Revolut correspondante a eu lieu le mois suivant. "
            "Vous pouvez les mettre en mémoire ici pour qu'elles participent au matching du mois prochain."
        )

        # =========================
        # Affichage des KO BO de fin de mois (>= 25)
        # =========================

        df_ko_bo = st.session_state.get("ko_bo_final", pd.DataFrame()).copy()

        if df_ko_bo.empty:
            st.warning("Aucun KO BackOffice disponible. Lancez d'abord le matching.")
        else:
            df_ko_bo["Date"] = pd.to_datetime(df_ko_bo["Date"], errors="coerce")
            df_fin_mois = df_ko_bo[df_ko_bo["Date"].dt.day >= 25].copy()

            if df_fin_mois.empty:
                st.warning("Aucun KO BackOffice avec une date >= 25 du mois.")
            else:
                st.markdown(f"**{len(df_fin_mois)} dépenses BO de fin de mois détectées :**")

                cols_affich = [c for c in [
                    "Date", "Montant", "Libelle", "Compte", "NumCompta", "idx_bo"
                ] if c in df_fin_mois.columns]

                df_fin_mois_view = df_fin_mois[cols_affich].copy()
                df_fin_mois_view.insert(0, "Mémoriser", True)

                edited_carryover = st.data_editor(
                    df_fin_mois_view,
                    column_config={
                        "Mémoriser": st.column_config.CheckboxColumn(
                            "Mémoriser ?",
                            help="Cochez pour inclure cette dépense dans le matching du mois prochain",
                            default=True,
                        ),
                        "idx_bo": None,
                        "Date": st.column_config.DateColumn("Date", format="DD/MM/YYYY"),
                        "Montant": st.column_config.NumberColumn("Montant", format="%.2f €"),
                    },
                    use_container_width=True,
                    hide_index=True,
                    key="editor_carryover",
                    disabled=[c for c in df_fin_mois_view.columns if c != "Mémoriser"]
                )

                col_btn1, col_btn2 = st.columns(2)

                with col_btn1:
                    if st.button("💾 Mettre en mémoire les dépenses sélectionnées"):
                        lignes_selectionnees = edited_carryover[edited_carryover["Mémoriser"] == True]

                        if lignes_selectionnees.empty:
                            st.warning("Aucune ligne sélectionnée.")
                        else:
                            idx_bo_selectionnes = df_fin_mois[
                                df_fin_mois.index.isin(lignes_selectionnees.index)
                            ]["idx_bo"].tolist()

                            df_a_memoriser = df_ko_bo[
                                df_ko_bo["idx_bo"].isin(idx_bo_selectionnes)
                            ].copy()

                            # ✅ Extraction du Payer depuis le Libelle
                            def extraire_payer_depuis_libelle(libelle):
                                for nom in mail_mapping.keys():
                                    prenom = nom.split()[0]
                                    if prenom.lower() in str(libelle).lower():
                                        return nom
                                return ""

                            df_a_memoriser["Payer"] = df_a_memoriser["Libelle"].apply(
                                extraire_payer_depuis_libelle
                            )

                            # Log temporaire pour vérification
                            payers_trouves = [p for p in df_a_memoriser["Payer"].unique().tolist() if p]
                            st.write(f"Payers détectés : {payers_trouves}")

                            if df_a_memoriser["Payer"].eq("").all():
                                st.warning(
                                    "Aucun concierge détecté dans les libellés. "
                                    "Vérifiez les libellés BO."
                                )
                            elif save_carryover_to_sheet(df_a_memoriser):
                                st.session_state["bo_carryover"] = df_a_memoriser
                                st.success(
                                    f"✅ {len(df_a_memoriser)} dépenses sauvegardées pour : "
                                    f"{', '.join(payers_trouves)}"
                                )

                with col_btn2:
                    if st.button("🗑️ Vider la mémoire inter-mois"):
                        if clear_carryover_from_sheet():
                            st.session_state.pop("bo_carryover", None)
                            st.success("🗑️ Mémoire vidée.")
                            st.rerun()

        # =========================
        # Affichage de ce qui est actuellement en mémoire (Google Sheets)
        # =========================
        st.markdown("---")
        st.subheader("📋 Dépenses actuellement en mémoire")

        df_mem = load_carryover_from_sheet()

        if df_mem.empty:
            st.info("Aucune dépense en mémoire pour le moment.")
        else:
            st.success(
                f"**{len(df_mem)} dépenses** en mémoire — elles seront automatiquement "
                f"incluses dans le prochain matching."
            )
            cols_mem = [c for c in [
                "Date", "Montant", "Libelle", "Payer", "Compte", "NumCompta"
            ] if c in df_mem.columns]
            st.dataframe(df_mem[cols_mem], use_container_width=True, hide_index=True)


if __name__ == "__main__":
    # Lancement autonome : on configure la page nous-mêmes
    # (quand le module est importé comme page, c'est l'app principale qui le fait).
    st.set_page_config(
        page_title="Rapprochement Bancaire IA",
        page_icon="💳",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    run_interface()