# =========================
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
        df_current = st.session_state.df_source.copy()

        # 🚫 Supprime les colonnes 'Devise' et 'Original Amount' si elles existent
        cols_to_drop = [col for col in ["Devise", "Original Amount",'Concierge'] if col in df_current.columns]
        if cols_to_drop:
            df_current.drop(columns=cols_to_drop, inplace=True)

        # 📥 Conversion vers Excel sans les noms de colonnes
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            # index=False pour ne pas exporter l'index
            # header=False pour ne pas exporter les noms de colonnes 👇
            df_current.to_excel(writer, index=False, header=False)
        buf.seek(0)

        # 🖱️ Bouton de téléchargement
        with st.sidebar:
            st.markdown("### 📅 Export permanent")
            st.download_button(
                "📅 Télécharger maintenant",
                data=buf,
                file_name="achats_export.xlsx",
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

def suppression_caracteres_speciaux(df):
    df["Libelle"] = df["Libelle"].str.replace(r"[^\w\sÀ-ÿ]", "", regex=True)
    return ("✅ La colonne Libelle ne comprend plus de caractères spéciaux.")


def check_devise(df):
    if ((df["Débit(€)"]==0).all()) & ((df["Crédit (€)"]==0).all()) & ((df["Devise"]!='€').all()) :
        return True
    else:
        return False

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
    
def euros_zeros(df):
    # Vérifie si la devise est l'euro ET que tout est à 0
    if (df["Devise"] == "€").any() and df["Débit(€)"].sum() == 0 and df["Crédit (€)"].sum() == 0:
        return True
    else:
        return False



def check_lignes_comptables(df):
    df['Débit(€)']=df['Débit(€)'].fillna(0)
    df['Crédit (€)']=df['Crédit (€)'].fillna(0)
    num_piece=df["n° de piece"].unique()
    log_generale=[]
    Compte_Tiers_invalide=0
    achats_ko=[]
    for i in num_piece:
        log_piece=[]
        log_ko=False
        df_provisoire=df[(df["n° de piece"]==i)]
        if check_devise(df_provisoire):
            if check_compte_tiers_invalide(df_provisoire):
                log_piece.append("Compte Tiers invalide")
                Compte_Tiers_invalide+=1
                log_ko=True
                achats_ko.append(i)
            date_facture=df_provisoire.iloc[0]['Date Facture']
            devise=df_provisoire.iloc[0]['Devise']
            original_amount=df_provisoire.iloc[0]['Original Amount']
            rate=get_conversion_rate_frankfurter(date_facture,devise)
            #hey
            if rate!=False :
                log_piece.append(f'🔧 Conversion de la devise effectue. Devise : {devise}, Date : {date_facture}, Taux : {rate}, Montant : {original_amount}')
                if df_provisoire.iloc[0]['Original Amount']>0:
                    df.loc[df_provisoire.index[0],'Crédit (€)']=rate*original_amount
                    df.loc[df_provisoire.index[1],'Débit(€)']=rate*original_amount
                    df.loc[df_provisoire.index[2],'Débit(€)']=rate*original_amount
                else:
                    df.loc[df_provisoire.index[0],'Débit(€)']=rate*original_amount*(-1)
                    df.loc[df_provisoire.index[1],'Crédit (€)']=rate*original_amount*(-1)
                    df.loc[df_provisoire.index[2],'Crédit (€)']=rate*original_amount*(-1)
            else:
                log_piece.append(f"Ce numero de piece a besoin d'une conversion de la devise manuelle ,  Devise : {devise}, Date : {date_facture}, Montant : {original_amount} ")
                log_ko=True

            if log_ko==False:
             log_generale.append(f"✅ Achat {i} : OK , {log_piece}")
            if log_ko==True:
                log_generale.append(f"❌ Achat {i} : KO , {log_piece}")
            continue
        df_provisoire=df[(df["n° de piece"]==i) & (df["Code"]=="G")]
        if check_compte_tiers_invalide(df_provisoire):
            log_piece.append("Compte Tiers invalide")
            Compte_Tiers_invalide+=1
            log_ko=True
            achats_ko.append(i)

        if euros_zeros(df_provisoire):
            log_piece.append("Erreur ! Crédit et débit sont à 0. Vérifiez et corrigez manuellement. ")
            log_ko=True
            log_generale.append(f"❌ Achat {i} : KO , {log_piece}")
            continue

        
        if check_oublie_credit_ou_debit(df_provisoire):
            df.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Débit(€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'].sum()
            df.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Crédit (€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Débit(€)'].sum()
            df_provisoire.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Débit(€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'].sum()
            df_provisoire.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Crédit (€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Débit(€)'].sum()
            log_piece.append(f"🔧 Achat {i}- Credit ou Debit omis")

        if check_credit_egale_debit(df_provisoire)==False:
            log_piece.append(f"Credit≠Debit {df_provisoire['Débit(€)'].sum()} -- {df_provisoire['Crédit (€)'].sum()}")
            log_ko=True
        else:
            if check_lignes_vides(df_provisoire)==False:
                idx_a_supprimer = get_index_lignes_vides(df_provisoire)
                df.drop(index=idx_a_supprimer,inplace=True)
                log_piece.append(f"🔧 ligne vide supprime")

            if check_mauvais_emplacement_credit(df_provisoire)==True:
                log_piece.append(f" 🔧 mauvais emplacement credit modifie")
                index_cond = df_provisoire[df_provisoire['Compte Généraux']!=401000].index[df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'] != 0]
                credit_tmp = df.loc[index_cond, 'Crédit (€)'].copy()
                df.loc[index_cond, 'Crédit (€)'] = df.loc[index_cond, 'Débit(€)']*(-1)
                df.loc[index_cond, 'Débit(€)']  = credit_tmp * (-1)
                
            if check_mauvais_emplacement_debit(df_provisoire)==True:
                log_piece.append(f"🔧 mauvais emplacement debit modifie")
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
        log_debut.append(suppression_caracteres_speciaux(df))

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
                df_unique[["n° de piece", "Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge","Date Facture"]],
                key=editor_key,
                hide_index=True,
            )

            if st.button("✅ Valider les corrections", key=validate_key):
                api_logs = []
                for _, r in edited.iterrows():
                    if r['Compte Tiers'] != "???":
                        idx = df[
                            (df["Libelle"] == r["Libelle"]) 
                            & (df["Compte Généraux"] == 401000)
                        ].index
                        if not idx.empty:
                            df.loc[idx, ["Compte Tiers"]] = r[["Compte Tiers"]].values
                        with st.spinner("Mise à jour des comptes tiers dans le CRM (seulement les lignes modifiées)…"):
                            invoice_number = str(r["n° de piece"]).strip()
                            compte_value = str(r["Compte Tiers"]).strip()
                            date = _to_iso_date(str(r["Date Facture"]).strip())
                                
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

                with st.expander("Détails des mises à jour CRM"):
                    for line in api_logs:
                        st.write(line)


                st.session_state.df_source = df
                st.success("✅ Modifications enregistrées. Clique sur le bouton ci-dessous pour relancer le contrôle.")

                if st.button("🔁 Relancer le contrôle", key=rerun_key):
                    # relancer uniquement la fonction (pas de sys.exit)
                    log_generale, Compte_Tiers_invalide, achats_ko = check_lignes_comptables(df)
                    st.session_state.ko_cycle += 1  # ➜ nouvelles clés uniques au prochain rendu
                    st.success("Le contrôle a été relancé ✅")
                    st.experimental_rerun()  # force un rerender propre

