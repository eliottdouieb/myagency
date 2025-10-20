# =========================
# ====== TES IMPORTS ======
# =========================
from xlsx2csv import Xlsx2csv
from io import StringIO
import math
import pandas as pd
import numpy as np
from datetime import datetime
import requests
import streamlit as st
from io import BytesIO


# ====== API CRM HELPERS (secrets-aware) ======


def _crm_cfg():
    try:
        cfg = st.secrets["crm"]
        base_url = cfg["base_url"].rstrip("/")
        email = cfg["email"]
        password = cfg["password"]
        return base_url, email, password
    except Exception as e:
        raise RuntimeError(
            "Secrets CRM manquants. Ajoute dans .streamlit/secrets.toml :\n"
            "[crm]\nbase_url=\"https://preprod.api-concierge.mybackoffice.fr\"\n"
            "email=\"...\"\npassword=\"...\"\n"
        ) from e

def _endpoints():
    base_url, *_ = _crm_cfg()
    AUTH_URL = f"{base_url}/api/appMember/concierge/login"
    ACCOUNTING_URL_TMPL = f"{base_url}/api/myagency/controller/accounting/{{ConciergeHash}}"
    return AUTH_URL, ACCOUNTING_URL_TMPL

def crm_login(force: bool = False):
    """Login CRM (met en cache ApiToken/ConciergeHash dans st.session_state)."""
    if (not force) and "ApiToken" in st.session_state and "ConciergeHash" in st.session_state:
        return st.session_state["ApiToken"], st.session_state["ConciergeHash"]

    AUTH_URL, _ = _endpoints()
    _, email, password = _crm_cfg()

    resp = requests.post(AUTH_URL, json={"email": email, "password": password}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Login failed: {data}")

    ApiToken = str(data.get("ApiToken", "")).strip()
    ConciergeHash = str(data.get("ConciergeHash", "")).strip()
    if not ApiToken or not ConciergeHash:
        raise RuntimeError("Missing ApiToken/ConciergeHash in login response.")

    st.session_state["ApiToken"] = ApiToken
    st.session_state["ConciergeHash"] = ConciergeHash
    return ApiToken, ConciergeHash

def _to_iso_date(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(dt, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return None
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None

def push_accounting_update(invoice_number: str, new_value: str,
                           *, type_: str = "member", field_: str = "achat",
                           date_iso: str | None = None):
    """Envoie 1 update au CRM (changement de compte tiers d'un achat)."""
    ApiToken, ConciergeHash = crm_login()
    _, ACCOUNTING_URL_TMPL = _endpoints()
    url = ACCOUNTING_URL_TMPL.format(ConciergeHash=ConciergeHash)

    payload = {
        "payload": {
            "InvoiceNumber": str(invoice_number),
            "type": type_,           # "member" (par défaut, comme ton appel qui marche) ou "partner"
            "field": field_,         # "achat"
            "value": str(new_value)  # ex: "4010000234"
        }
    }
    if date_iso:
        payload["payload"]["date"] = date_iso

    headers = {"Content-Type": "application/json", "ApiToken": ApiToken}
    r = requests.post(url, json=payload, headers=headers, timeout=20)
    return r



def safe_read_excel(uploaded, header_row: int = 1) -> pd.DataFrame:
    try:
        return pd.read_excel(uploaded, header=header_row, engine="openpyxl")
    except Exception as err:
        # st.warning(f"openpyxl a échoué ; utilisation de xlsx2csv → {err}")
        from xlsx2csv import Xlsx2csv
        uploaded.seek(0)
        csv_buffer = StringIO()
        Xlsx2csv(BytesIO(uploaded.read()), outputencoding="utf-8").convert(csv_buffer)
        csv_buffer.seek(0)
        return pd.read_csv(csv_buffer, header=header_row)
    
def show_sidebar_download():
    if "df_source" in st.session_state and st.session_state.df_source is not None:
        df_current = st.session_state.df_source.copy()  # 🔄 récupère toujours l'état actuel
        with st.sidebar:
            st.markdown("### 📅 Export permanent")
            st.download_button(
                "📅 Télécharger maintenant",
                dataframe_to_excel_bytes(df_current),
                "achats_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_sidebar_anytime"
            )

def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

def _get_df_to_export_anytime(df) -> pd.DataFrame:
    return (df)
    

def inc(code: str) -> str:
        mois, num = code.split("-")
        return f"{mois}-{int(num)+1}"

def remplie_numero_piece_manquant(df):
    last_code = None
    for i, row in df.iterrows():
            cur = df.at[i, "n° de piece"]
            if row["Compte Généraux"] == 401000:
                if pd.isna(cur) or cur.strip() == "":
                    new_code = "01-01" if last_code is None else inc(last_code)
                    df.at[i, "n° de piece"] = new_code
                    last_code = new_code
                else:
                    last_code = cur.strip()
            else:
                if (pd.isna(cur) or cur.strip() == "") and last_code:
                    df.at[i, "n° de piece"] = last_code
    return("✅ Les n° de pièce manquants ont été remplis automatiquement.")


def suppression_445660_dans_Compte_tiers(df):
    mask_445 = (df["Compte Généraux"] == 445660) & (df["Compte Tiers"] == '445660')
    df.loc[mask_445, "Compte Tiers"] = np.nan
    return("✅ La colonne Compte Tiers ne comprend plus de 445660 mal placés.")


def check_credit_egale_debit(df):
    if math.floor((df["Débit(€)"].sum()* 100) / 100)==math.floor((df["Crédit (€)"].sum()* 100) / 100):
        return True
    else:
        return False

def check_compte_tiers_invalide(df):
    comptes = df[df['Compte Généraux'] == 401000]['Compte Tiers'].astype(str)
    # Vérifie si au moins un ne commence PAS par "401"
    return (~comptes.str.match(r"^401")).any()

def get_index_lignes_vides(df):
    d = df[df['Compte Généraux'] != 401000]
    mask = (d['Débit(€)'] == 0) & (d['Crédit (€)'] == 0)
    return d[mask].index

def check_lignes_vides(df):
    return get_index_lignes_vides(df).empty

def check_oublie_credit_ou_debit(df):
    a=df[df['Compte Généraux']==401000]
    b=df[df['Compte Généraux']!=401000]
    if ((a['Débit(€)']==0)&(a['Crédit (€)']==0)).any():
        return True
    else:
        return False
    
def check_mauvais_emplacement(df):
    a=df[df['Compte Généraux']==401000]
    b=df[df['Compte Généraux']!=401000]
    if ((a['Débit(€)']!=0)&(b['Débit(€)']!=0)).any() or ((a['Crédit (€)']!=0)&(b['Crédit (€)']!=0)).any():
        return True
    else:
        return False
    
def check_mauvais_emplacement_credit(df):
    a=df[df['Compte Généraux']==401000]
    b=df[df['Compte Généraux']!=401000]
    if ((a['Crédit (€)']!=0).any() & (b['Crédit (€)']!=0).any()) :
        return True
    else:
        return False
    
def check_mauvais_emplacement_debit(df):
    a=df[df['Compte Généraux']==401000]
    b=df[df['Compte Généraux']!=401000]
    if ((a['Débit(€)']!=0).any() & (b['Débit(€)']!=0).any()):
        return True
    else:
        return False



def check_lignes_comptables(df):
    num_piece=df["n° de piece"].unique()
    log_generale=[]
    Compte_Tiers_invalide=0
    achats_ko=[]
    for i in num_piece:
        log_piece=[]
        log_ko=False
        df_provisoire=df[(df["n° de piece"]==i) & (df["Code"]=="G")]
        if check_compte_tiers_invalide(df_provisoire):
            log_piece.append("Compte Tiers invalide")
            Compte_Tiers_invalide+=1
            log_ko=True
            achats_ko.append(i)

        if check_oublie_credit_ou_debit(df_provisoire):
            df.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Débit(€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'].sum()
            df.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Crédit (€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Débit(€)'].sum()
            df_provisoire.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Débit(€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'].sum()
            df_provisoire.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Crédit (€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Débit(€)'].sum()
            log_piece.append(f"Achat {i}- Credit ou Debit omis")

        if check_credit_egale_debit(df_provisoire)==False:
            log_piece.append(f"Credit≠Debit {df_provisoire['Débit(€)'].sum()} -- {df_provisoire['Crédit (€)'].sum()}")
            log_ko=True
        else:
            if check_lignes_vides(df_provisoire)==False:
                idx_a_supprimer = get_index_lignes_vides(df_provisoire)
                df.drop(index=idx_a_supprimer,inplace=True)
                log_piece.append(f"ligne vide supprime")

            if check_mauvais_emplacement_credit(df_provisoire)==True:
                log_piece.append(f"mauvais emplacement credit modifie")
                index_cond = df_provisoire[df_provisoire['Compte Généraux']!=401000].index[df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'] != 0]
                credit_tmp = df.loc[index_cond, 'Crédit (€)'].copy()
                df.loc[index_cond, 'Crédit (€)'] = df.loc[index_cond, 'Débit(€)']*(-1)
                df.loc[index_cond, 'Débit(€)']  = credit_tmp * (-1)
                
            if check_mauvais_emplacement_debit(df_provisoire)==True:
                log_piece.append(f"mauvais emplacement debit modifie")
                index_cond = df_provisoire[df_provisoire['Compte Généraux']!=401000].index[df_provisoire[df_provisoire['Compte Généraux']!=401000]['Débit(€)'] != 0]
                credit_tmp = df.loc[index_cond, 'Débit(€)'].copy()
                df.loc[index_cond, 'Débit(€)'] = df.loc[index_cond, 'Crédit (€)']*(-1)
                df.loc[index_cond, 'Crédit (€)']  = credit_tmp * (-1)


        if log_ko==False:
             log_generale.append(f"✅ Achat {i} : OK , {log_piece}")
        if log_ko==True:
            log_generale.append(f"❌ Achat {i} : KO , {log_piece}")

    return log_generale,Compte_Tiers_invalide,achats_ko



def run_interface():
    st.title("📊 Contrôle automatique des écritures d'achats")



    uploaded = st.file_uploader("Importe ton fichier Excel des achats", type=["xlsx"])

    if uploaded:
        if "df_source" not in st.session_state:
            st.session_state.df_source = safe_read_excel(uploaded, header_row=1)

        df = st.session_state.df_source

        log_debut=[]
        log_debut.append(remplie_numero_piece_manquant(df))
        log_debut.append(suppression_445660_dans_Compte_tiers(df))

        log_generale,Compte_Tiers_invalide,achats_ko=check_lignes_comptables(df)
        st.session_state.df_source = df

        show_sidebar_download() # bouton dispo tout le temps à gauche


        st.subheader("📝 Logs")
        st.code("\n".join(log_debut), language="text")
        st.code("\n".join(log_generale), language="text")

        # init une seule fois au chargement
        if "ko_cycle" not in st.session_state:
            st.session_state.ko_cycle = 0

        # ... après tes calculs:
        if Compte_Tiers_invalide > 0:
            st.warning(
                "Des achats KO subsistent (Compte Tiers invalide). "
                "Modifie le tableau puis clique sur « Valider les corrections »."
            )

            df_ko = df[(df["n° de piece"].isin(achats_ko)) & (df["Compte Généraux"] == 401000)].copy()
            df_unique = df_ko.drop_duplicates(subset="Libelle").copy()

            editor_key = f"ko_editor_{st.session_state.ko_cycle}"
            validate_key = f"validate_{st.session_state.ko_cycle}"
            rerun_key = f"rerun_{st.session_state.ko_cycle}"

            edited = st.data_editor(
                df_unique[["n° de piece", "Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge"]],
                key=editor_key,
                hide_index=True,
            )

            if st.button("✅ Valider les corrections", key=validate_key):
                # Clé d’alignement sûre: (Libelle, n° de piece)
                key_cols = ["Libelle", "n° de piece"]
                cols_edit = ["Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge"]

                df_unique_keyed = df_unique.set_index(key_cols)
                edited_keyed = edited.set_index(key_cols)

                # Lignes réellement modifiées
                changed_idx = []
                for k in edited_keyed.index:
                    if k not in df_unique_keyed.index:
                        changed_idx.append(k)
                    else:
                        before = df_unique_keyed.loc[k, ["Compte Tiers", "Débit(€)", "Crédit (€)", "Concierge"]].to_dict()
                        after  = edited_keyed.loc[k, ["Compte Tiers", "Débit(€)", "Crédit (€)", "Concierge"]].to_dict()
                        if any(before[c] != after[c] for c in after.keys()):
                            changed_idx.append(k)

                # Applique les modifs dans df
                for (lib, piece) in changed_idx:
                    r = edited_keyed.loc[(lib, piece)]
                    idx = df[
                        (df["Libelle"] == lib) &
                        (df["Compte Généraux"] == 401000) &
                        (df["n° de piece"] == piece)
                    ].index
                    if not idx.empty:
                        df.loc[idx, ["Compte Tiers", "Débit(€)", "Crédit(€)", "Libelle", "Concierge"]] = \
                            r[["Compte Tiers", "Débit(€)", "Crédit(€)", "Libelle", "Concierge"]].values

                # Push CRM si "Compte Tiers" a changé
                pushes_ok, pushes_ko = 0, 0
                for (lib, piece) in changed_idx:
                    old_ct = df_unique_keyed.loc[(lib, piece), "Compte Tiers"] if (lib, piece) in df_unique_keyed.index else None
                    new_ct = edited_keyed.loc[(lib, piece), "Compte Tiers"]
                    if str(old_ct) != str(new_ct):
                        # Cherche la date facture si dispo (optionnel)
                        date_iso = None
                        if "Date Facture" in df.columns:
                            row0 = df[(df["Libelle"] == lib) & (df["n° de piece"] == piece)].head(1)
                            if not row0.empty:
                                date_iso = _to_iso_date(row0.iloc[0]["Date Facture"])

                        try:
                            # "member" = cohérent avec ton exemple qui marche ; mets "partner" si nécessaire selon les cas
                            resp = push_accounting_update(
                                invoice_number=piece,
                                new_value=new_ct,
                                type_="member",
                                field_="achat",
                                date_iso=date_iso
                            )
                            if 200 <= resp.status_code < 300:
                                st.toast(f"✔ CRM mis à jour — {piece}: Compte Tiers → {new_ct}", icon="✅")
                                pushes_ok += 1
                            else:
                                st.error(f"❌ CRM échec {piece} (HTTP {resp.status_code}) — {resp.text[:240]}")
                                pushes_ko += 1
                        except Exception as e:
                            st.error(f"❌ CRM exception {piece}: {e}")
                            pushes_ko += 1

                st.session_state.df_source = df
                st.success(f"✅ Modifs enregistrées. Push CRM: {pushes_ok} OK / {pushes_ko} KO.")

                if st.button("🔁 Relancer le contrôle", key=rerun_key):
                    log_generale, Compte_Tiers_invalide, achats_ko = check_lignes_comptables(df)
                    st.session_state.ko_cycle += 1
                    st.success("Le contrôle a été relancé ✅")
                    st.experimental_rerun()
