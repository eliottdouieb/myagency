#  =========================
# ====== TES IMPORTS ======
# =========================
from xlsx2csv import Xlsx2csv
from io import StringIO
import math
import pandas as pd
import numpy as np
from datetime import datetime,date
import requests
import streamlit as st
from io import BytesIO
import time

import re

def normalize_invoice(v):
    """Ex: 54147.0 → '54147'"""
    s = str(v).strip()
    return re.sub(r"\.0$", "", s)

def normalize_account_client(v):
    """Ex: ' 411lo ' → '411LO'"""
    return str(v).strip().upper()


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

        
def run_api_crm(invoice,value,date):
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
            "InvoiceNumber": invoice,
            "type": "member",
            "field": "vente",
            "value": value,
            "date":date
        }
    }

    headers = {
        "Content-Type": "application/json",
        "ApiToken": ApiToken,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=15)

    # print("HTTP:", resp.status_code)
    ctype = (resp.headers.get("content-type") or "").lower()
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
@st.cache_data(show_spinner=False, ttl=1800)

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
                "ventes_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_sidebar_anytime"
            )

def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf


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



def check_Currency(df):
    if ((df["Debit"]==0).all()) & ((df["Credit"]==0).all()) & ((df["Currency"]!='€').all()) :
        return True
    else:
        return False

def check_credit_egale_debit(df):
    if math.floor((df["Debit"].sum()* 100) / 100)==math.floor((df["Credit"].sum()* 100) / 100):
        return True
    else:
        return False

def check_compte_tiers_invalide(df):
    comptes = df[df['Account General'] == 411000]['Account Client'].astype(str)
    # Vérifie si au moins un ne commence PAS par "411"
    return (comptes=='411-NO MEMBER ACCOUNT').any()

def get_index_lignes_vides(df):
    d = df[df['Account General'] != 411000]
    mask = (d['Debit'] == 0) & (d['Credit'] == 0)
    return d[mask].index

def check_lignes_vides(df):
    return get_index_lignes_vides(df).empty

def check_oublie_credit_ou_debit(df):
    a=df[df['Account General']==411000]
    b=df[df['Account General']!=411000]
    if ((a['Debit']==0)&(a['Credit']==0)).any():
        return True
    else:
        return False
    
def check_mauvais_emplacement(df):
    a=df[df['Account General']==411000]
    b=df[df['Account General']!=411000]
    if ((a['Debit']!=0)&(b['Debit']!=0)).any() or ((a['Credit']!=0)&(b['Credit']!=0)).any():
        return True
    else:
        return False
    
def check_mauvais_emplacement_credit(df):
    a=df[df['Account General']==411000]
    b=df[df['Account General']!=411000]
    if ((a['Credit']!=0).any() & (b['Credit']!=0).any()) :
        return True
    else:
        return False
    
def check_mauvais_emplacement_debit(df):
    a=df[df['Account General']==411000]
    b=df[df['Account General']!=411000]
    if ((a['Debit']!=0).any() & (b['Debit']!=0).any()):
        return True
    else:
        return False

def check_facture_0(df):
    # ligne_411 = df[df["Account General"].astype(str).str.strip() == "411000"]
    # l411 = ligne_411.iloc[0]
    # autres = df[df["Account General"].astype(str).str.strip() != "411000"]
    
    total_debit = df["Debit"].sum()
    total_credit = df["Credit"].sum()

    if total_debit == 0 and total_credit == 0:
        return True
    
    else:
        return False


def check_lignes_comptables(df):
    df['Debit']=df['Debit'].fillna(0)
    df['Credit']=df['Credit'].fillna(0)
    invoice=df["#"].unique()
    log_generale=[]
    Compte_Tiers_invalide=0
    ventes_ko=[]
    for i in invoice:
        log_piece=[]
        log_ko=False
        df_provisoire=df[(df["#"]==i)]
        if check_facture_0(df_provisoire):
            if check_compte_tiers_invalide(df_provisoire):
                log_piece.append("Account Client invalide")
                Compte_Tiers_invalide+=1
                log_ko=True
                ventes_ko.append(i)

            log_piece.append("🔧 Erreur sur facture mise à 0 corrigée automatiquement") 
            ligne_411 = df_provisoire[df_provisoire["Account General"].astype(str).str.strip() == "411000"]
            if ligne_411.shape[0] == 1:
                l411 = ligne_411.iloc[0]
                autres = df_provisoire[df_provisoire["Account General"].astype(str).str.strip() != "411000"]

                idx_ligne_411 = ligne_411.index[0]
                df.loc[[idx_ligne_411, autres.index[0]], "Account General"] = l411["Account General"]
                df.loc[[idx_ligne_411, autres.index[0]], "Account Client"] = l411["Account Client"]

                df.loc[idx_ligne_411, "Debit"] = 1.0
                df.loc[idx_ligne_411, "Credit"] = 0.0
                df.loc[autres.index[0], "Debit"] = 0.0
                df.loc[autres.index[0], "Credit"] = 1.0

                if df.loc[idx_ligne_411, "Code"] != "G":
                    df.loc[idx_ligne_411, "Code"] = "G"
                if df.loc[autres.index[0], "Code"] != "G":
                    df.loc[autres.index[0], "Code"] = "G"

                lignes_a_supprimer = autres.index[1:]
                df.drop(lignes_a_supprimer, inplace=True)

            if log_ko==False:
                log_generale.append(f"✅ vente {i} : OK , {log_piece}")
            if log_ko==True:
                log_generale.append(f"❌ vente {i} : KO , {log_piece}")
            continue
            
        
        df_provisoire=df[(df["#"]==i) & (df["Code"]=="G")]
        if check_compte_tiers_invalide(df_provisoire):
            log_piece.append("Account Client invalide")
            Compte_Tiers_invalide+=1
            log_ko=True
            ventes_ko.append(i)
        
        if check_oublie_credit_ou_debit(df_provisoire):
            df.loc[df_provisoire[df_provisoire['Account General']==411000].index, "Debit"] = df_provisoire[df_provisoire['Account General']!=411000]['Credit'].sum()
            df.loc[df_provisoire[df_provisoire['Account General']==411000].index, "Credit"] = df_provisoire[df_provisoire['Account General']!=411000]['Debit'].sum()
            df_provisoire.loc[df_provisoire[df_provisoire['Account General']==411000].index, "Debit"] = df_provisoire[df_provisoire['Account General']!=411000]['Credit'].sum()
            df_provisoire.loc[df_provisoire[df_provisoire['Account General']==411000].index, "Credit"] = df_provisoire[df_provisoire['Account General']!=411000]['Debit'].sum()
            log_piece.append(f"🔧 vente {i}- Credit ou Debit omis")

        if round(df_provisoire["Debit"].sum(), 2) != round(df_provisoire["Credit"].sum(), 2):
            log_piece.append(
                f"Credit≠Debit {round(df_provisoire['Debit'].sum(), 2)} -- {round(df_provisoire['Credit'].sum(), 2)}"
            )
            log_ko = True

        else:
            if check_lignes_vides(df_provisoire)==False:
                idx_a_supprimer = get_index_lignes_vides(df_provisoire)
                df.drop(index=idx_a_supprimer,inplace=True)
                log_piece.append(f"🔧 ligne vide supprime")

            if check_mauvais_emplacement_credit(df_provisoire)==True:
                log_piece.append(f" 🔧 mauvais emplacement credit modifie")
                index_cond = df_provisoire[df_provisoire['Account General']!=411000].index[df_provisoire[df_provisoire['Account General']!=411000]['Credit'] != 0]
                credit_tmp = df.loc[index_cond, 'Credit'].copy()
                df.loc[index_cond, 'Credit'] = df.loc[index_cond, 'Debit']*(-1)
                df.loc[index_cond, 'Debit']  = credit_tmp * (-1)
                
            if check_mauvais_emplacement_debit(df_provisoire)==True:
                log_piece.append(f"🔧 mauvais emplacement debit modifie")
                index_cond = df_provisoire[df_provisoire['Account General']!=411000].index[df_provisoire[df_provisoire['Account General']!=411000]['Debit'] != 0]
                credit_tmp = df.loc[index_cond, 'Debit'].copy()
                df.loc[index_cond, 'Debit'] = df.loc[index_cond, 'Credit']*(-1)
                df.loc[index_cond, 'Credit']  = credit_tmp * (-1)

        if log_ko==False:
             log_generale.append(f"✅ vente {i} : OK , {log_piece}")
        if log_ko==True:
            log_generale.append(f"❌ vente {i} : KO , {log_piece}")

    return log_generale,Compte_Tiers_invalide,ventes_ko


def run_interface():
    st.title("📊 Contrôle automatique des écritures des ventes")



    uploaded = st.file_uploader("Importe ton fichier Excel des ventes", type=["xlsx"])

    if uploaded:
        if "df_source" not in st.session_state:
            st.session_state.df_source = safe_read_excel(uploaded, header_row=1)

        df = st.session_state.df_source

        log_generale,Compte_Tiers_invalide,ventes_ko=check_lignes_comptables(df)
        st.session_state.df_source = df

        show_sidebar_download() # bouton dispo tout le temps à gauche


        st.subheader("📝 Logs")
        st.code("\n".join(log_generale), language="text")

        # init une seule fois au chargement
        if "ko_cycle" not in st.session_state:
            st.session_state.ko_cycle = 0

        # ... après tes calculs:
        if Compte_Tiers_invalide > 0:
            st.warning(
                "Des ventes KO subsistent (Account Client invalide). "
                "Modifie le tableau puis clique sur « Valider les corrections »."
            )

            df_ko = df[(df["#"].isin(ventes_ko)) & (df["Account General"] == 411000)].copy()
            df_unique = df_ko.drop_duplicates(subset="Name").copy()

            editor_key = f"ko_editor_{st.session_state.ko_cycle}"
            validate_key = f"validate_{st.session_state.ko_cycle}"
            rerun_key = f"rerun_{st.session_state.ko_cycle}"

            edited = st.data_editor(
                df_unique[["#", "Account Client", "Debit", "Credit", "Name", "Concierge","Date"]],
                key=editor_key,
                hide_index=True,
            )

            if st.button("✅ Valider les corrections", key=validate_key):
                ajout_crm=[]
                api_logs = []
                for _, r in edited.iterrows():
                    if r['Account Client'] != "411-NO MEMBER ACCOUNT":
                        invoice_number = normalize_invoice(r["#"])
                        compte_value = normalize_account_client(r["Account Client"])
                        date = _to_iso_date(r["Date"])
                        ajout_crm.append([invoice_number, compte_value, date])
                        idx = df[
                            (df["Name"] == r["Name"]) 
                            & (df["Account General"] == 411000)
                        ].index
                        if not idx.empty:
                            df.loc[idx, ["Account Client"]] = r[["Account Client"]].values

                with st.spinner("Mise à jour des comptes tiers dans le CRM (seulement les lignes modifiées)…"):
                    for i in ajout_crm:
                        result = run_api_crm(i[0], i[1], i[2])
                        st.write(result)
                        if result["status"] and 200 <= result["status"]  < 300:
                            if result["success"]==False:
                                if result["message"]=="Line not updated, same value":
                                    api_logs.append(f"❌ CRM ko — numéro de piece {i[0]} → {i[1]} (HTTP {result['status'] }, {result['success']},{result['message']}) | Account Client identique sur CRM donc pas de mise a jour")
                                else :
                                    api_logs.append(f"❌ CRM ko — numéro de piece {i[0]} → {i[1]} (HTTP {result['status'] }) | Numero de piece non existant")
                            else:
                                api_logs.append(f"✅ CRM ok — numéro de pieceeee {i[0]} → {i[1]} (HTTP {result['status'] }, {result['success']},{result['message']})")
                        else:
                            api_logs.append(f"❌ CRM ko — numéro de piece {i[0]} → {i[1]} (HTTP {result['status'] }) | {result['body'] }")
                        time.sleep(0.5)

                with st.expander("Détails des mises à jour CRM"):
                    for line in api_logs:
                        st.write(line)

                st.session_state.df_source = df
                st.success("✅ Modifications enregistrées. Clique sur le bouton ci-dessous pour relancer le contrôle.")

                if st.button("🔁 Relancer le contrôle", key=rerun_key):
                    # relancer uniquement la fonction (pas de sys.exit)
                    log_generale, Compte_Tiers_invalide, ventes_ko = check_lignes_comptables(df)
                    st.session_state.ko_cycle += 1  # ➜ nouvelles clés uniques au prochain rendu
                    st.success("Le contrôle a été relancé ✅")
                    st.experimental_rerun()  # force un rerender propree

