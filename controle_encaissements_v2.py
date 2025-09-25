import streamlit as st
import pandas as pd
from io import BytesIO, StringIO
import re
from datetime import datetime

PAYMENT_DICT = {
    "CHÈQUE": ("CH", 1),
    "CARTE BANCAIRE": ("CB", 2),
    "TPE CARTE BANCAIRE": ("CB", 2),
    "AMEX": ("AM", 3),
    "VIREMENT BANCAIRE": ("VI", 4),
    "CASH": ("CA", 5),
}

# ✅ Conversion pour téléchargement Excel
def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

def clean_name(name):
    return name.split('-')[0].strip()

def check_invoices(df):
    logs = []
    error = False
    invoices = df['Invoice #'].unique()
    for inv in invoices:
        sub_df = df[df['Invoice #'] == inv]
        debit_sum = sub_df['Debit'].sum()
        credit_sum = sub_df['Credit'].sum()
        debit_sum = round(debit_sum, 2)
        credit_sum = round(credit_sum, 2)

        sublogs = []
        is_ok = True

        # Rule A
        if debit_sum != credit_sum:
            sublogs.append(f"❌ Invoice {inv} : Debit ≠ Credit ({debit_sum} ≠ {credit_sum})")
            is_ok = False

        # Rule B - Vérification du format Account Global selon Payment Mean et mois
        try:
            payment_mean = sub_df['Payment Mean'].iloc[0].upper()
            second_row = sub_df.iloc[1]
            account_global = str(second_row['Account Global'])
            date_str = second_row['Payment Date']
            if isinstance(date_str, str):
                month = datetime.strptime(date_str, "%d/%m/%Y").month
            else:
                month = date_str.month

            expected_code = PAYMENT_DICT.get(payment_mean, (None, None))[1]
            if not (
                account_global.startswith("511")
                and len(account_global) >= 6
                and account_global[3] == str(expected_code)
                and account_global[-2:] == f"{month:02d}"
            ):
                sublogs.append(f"❌ Invoice {inv} : Account Global '{account_global}' doesn't match payment '{payment_mean}' rules for month {month:02d}")
                is_ok = False
        except Exception as e:
            sublogs.append(f"❌ Invoice {inv} : Erreur lecture règle payment → {e}")
            is_ok = False

        # Rule C
        for _, row in sub_df.iterrows():
            if row['Account Client'] == 411000 and row['Account Global'] == "411-NO MEMBER ACCOUNT":
                sublogs.append(f"❌ Invoice {inv} : Account Global is '411-NO MEMBER ACCOUNT' for 411000 client")
                is_ok = False
                break

        if is_ok:
            logs.append(f"✅ Invoice {inv} : OK")
        else:
            logs.extend(sublogs)
            error = True
    return logs, error


def apply_cb_to_amex_fix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Si pour une facture: Debit != Credit, et qu'il existe:
      - une ligne AG=627510 (commission)
      - une ligne AG=511207
    ET que Credit(627510) == 3% * Credit(511207),
    alors on applique:
      1) Colonne d'index 0 -> 'AM' (toutes les lignes de la facture)
      2) 'Account Global' 5112XX -> 5113XX (conserve les 2 derniers digits)
      3) Si une ligne a 'Account Global' vide, on ajoute à son 'Debit' la commission (Credit de 627510)
    """
    out = df.copy()

    for inv, g in out.groupby("Invoice #"):
        dsum = round(float(g["Debit"].sum()), 2)
        csum = round(float(g["Credit"].sum()), 2)
        if dsum == csum:
            continue  # rien à faire si déjà équilibré

        ag_str = g["Account Global"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()

        credit_627 = round(float(g.loc[ag_str == "627510", "Credit"].sum()), 2)
        credit_511207 = round(float(g.loc[ag_str == "511207", "Credit"].sum()), 2)

        # condition 3%
        if credit_627 == 0 or credit_511207 == 0:
            continue
        if round(credit_511207 * 0.03, 2) != credit_627:
            continue

        idxs = g.index
        first_col = out.columns[0]

        # 1) passer la 1ère colonne à 'AM' pour toute la facture
        out.loc[idxs, first_col] = "AM"

        # 2) transformer 5112XX -> 5113XX
        def _map_5112_to_5113(x):
            s = str(x).strip()
            s = re.sub(r"\.0$", "", s)
            m = re.fullmatch(r"5112(\d{2})", s)
            return f"5113{m.group(1)}" if m else x

        out.loc[idxs, "Account Global"] = out.loc[idxs, "Account Global"].apply(_map_5112_to_5113)

        # 3) ajouter la commission au 'Debit' de la (première) ligne AG vide
        ag_invoice = out.loc[idxs, "Account Global"]
        blank_idx = ag_invoice[ag_invoice.isna() | (ag_invoice.astype(str).str.strip() == "")].index
        if len(blank_idx) > 0:
            cur = pd.to_numeric(out.loc[blank_idx[0], "Debit"], errors="coerce")
            if pd.isna(cur):
                cur = 0.0
            out.loc[blank_idx[0], "Debit"] = round(float(cur) + credit_627, 2)

    return out


def transform_for_download(df):
    logs = []
    df = df.copy()
    
    # 1. Échanger colonne J et B (Payment Mean avec Account Global)
    df[['Date', 'Payment Date']] = df[['Payment Date', 'Date']]
    logs.append("🔁 Colonnes 'Date' et 'Payment Date' échangées.")

    # 2. Si Account Client = 411000, échanger avec Account Global
    mask = df['Account Client'] == 411000
    df.loc[mask, ['Account Client', 'Account Global']] = df.loc[mask, ['Account Global', 'Account Client']].values
    logs.append("🔁 Inversion 'Account Client' et 'Account Global' pour les lignes 411000.")

    # 3. Supprimer les colonnes J,K,L (Payment Mean, Payment Date, Comment)
    df.drop(columns=['Payment Mean', 'Date', 'Comment'], inplace=True)
    logs.append("🗑️ Colonnes 'Payment Mean', 'Date', 'Comment' supprimées.")

    # Mettre 'Payment Date' en 2e colonne (index 1)
    cols = list(df.columns)
    cols.insert(1, cols.pop(cols.index('Payment Date')))
    df = df[cols]

    return df, logs

def safe_read_excel(uploaded, header_row: int = 1) -> pd.DataFrame:
    try:
        return pd.read_excel(uploaded, header=header_row, engine="openpyxl")
    except Exception as err:
        st.warning(f"openpyxl a échoué ; utilisation de xlsx2csv → {err}")
        from xlsx2csv import Xlsx2csv
        uploaded.seek(0)
        csv_buffer = StringIO()
        Xlsx2csv(BytesIO(uploaded.read()), outputencoding="utf-8").convert(csv_buffer)
        csv_buffer.seek(0)
        return pd.read_csv(csv_buffer, header=header_row)

# =======================
# Interface (dans une fonction)
# =======================
def run_encaissements():
    st.title("🔍 Contrôle des écritures comptables - Encaissements")

    uploaded_file = st.file_uploader("📤 Upload ton fichier Excel (format tableau)", type=["xlsx", "xls", "csv"],key="uploader_encaissements_v2")

    # Init des flags d'état
    if "modifs_validees" not in st.session_state:
        st.session_state["modifs_validees"] = False
    if "df_source_encaissements" not in st.session_state and uploaded_file is not None:
        df_init = safe_read_excel(uploaded_file, header_row=1)
        # Nettoyage de la colonne Name (comportement initial conservé)
        df_init['Name'] = df_init['Name'].astype(str).apply(clean_name)
        st.success("🧽 Colonne 'Name' nettoyée (conservation avant le '-').")
        st.session_state["df_source_encaissements"] = df_init.copy()

    # Si on a une source, on affiche/contrôle
    if "df_source_encaissements" in st.session_state:
        df_current = st.session_state["df_source_encaissements"]

        # Recalcul des logs si nécessaire (ou première fois)
        if "controle_logs" not in st.session_state:
            logs, has_errors = check_invoices(df_current.copy())
            st.session_state["controle_logs"] = {
                "logs": logs,
                "has_errors": has_errors,
                "df": df_current.copy()
            }
        else:
            logs = st.session_state["controle_logs"]["logs"]
            has_errors = st.session_state["controle_logs"]["has_errors"]

        # Affichage des logs
        st.subheader("🧾 Résultats des vérifications")
        for log in st.session_state["controle_logs"]["logs"]:
            st.markdown(log)

        # Cas sans erreurs → export direct (comportement d’origine conservé)
        if not st.session_state["controle_logs"]["has_errors"]:
            # AVANT
            # df_export, export_logs = transform_for_download(st.session_state["controle_logs"]["df"])

            # APRES
            df_fixed = apply_cb_to_amex_fix(st.session_state["controle_logs"]["df"])
            df_export, export_logs = transform_for_download(df_fixed)

            st.success("✅ Toutes les vérifications sont OK.")
            buf = dataframe_to_excel_bytes(df_export)
            st.download_button(
                "📥 Télécharger le fichier corrigé",
                data=buf,
                file_name="encaissements_corrigés.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            for l in export_logs:
                st.info(l)

        # Cas avec erreurs → édition “411-NO MEMBER ACCOUNT” + relance
        else:
            df_checked = st.session_state["controle_logs"]["df"].copy()

            # ✅ AJOUT
            df_checked = apply_cb_to_amex_fix(df_checked)
            st.session_state["controle_logs"]["df"] = df_checked

            df_errors = df_checked[df_checked['Account Global'] == "411-NO MEMBER ACCOUNT"]

            # 🔄 NOUVEAU: si aucun enregistrement à corriger, ne pas afficher l'alerte ni l'éditeur
            if df_errors.empty:
                st.success("🎉 Aucune ligne avec '411-NO MEMBER ACCOUNT' à corriger.")

                # Préparer l'export avec les 3 modifications
                df_export = st.session_state["df_source_encaissements"].copy()

                # # 1) Échanger les valeurs entre les colonnes par position (index 1 et 10)
                # if df_export.shape[1] > 10:
                #     df_export.iloc[:, [1, 10]] = df_export.iloc[:, [10, 1]].to_numpy()

                # 2) Lorsque Account Client = 411000, inverser Account Client et Account Global (échange de valeurs)
                if "Account Client" in df_export.columns and "Account Global" in df_export.columns:
                    mask_swap = df_export["Account Client"] == 411000
                    df_export.loc[mask_swap, ["Account Client", "Account Global"]] = df_export.loc[mask_swap, ["Account Global", "Account Client"]].values

                # 3) Supprimer les 3 colonnes demandées : Payment Mean, Payment Date, Comment (si présentes)
                cols_to_drop = [c for c in ["Payment Mean", "Date", "Comment"] if c in df_export.columns]
                if cols_to_drop:
                    df_export.drop(columns=cols_to_drop, inplace=True)

                # Mettre 'Payment Date' en 2e colonne
                if "Payment Date" in df_export.columns:
                    cols = list(df_export.columns)
                    cols.insert(1, cols.pop(cols.index("Payment Date")))
                    df_export = df_export[cols]


                # Un seul bouton qui télécharge directement le fichier modifié
                buf = dataframe_to_excel_bytes(df_export)
                st.download_button(
                    "📤 Exporter le fichier excel corrigé",
                    data=buf,
                    file_name="encaissements_corrigés.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

            else:
                st.warning("⚠️ Il reste des lignes avec '411-NO MEMBER ACCOUNT'. Corrige-les ci-dessous.")

                # ==== ⬇️ API (via secrets) — drop-in replacement for encaissements ⬇️ ====
                # ==== ⬇️ API (corrigée) — encaissements ⬇️ ====
                import requests
                from datetime import datetime, date

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

                def _crm_base_url() -> str:
                    # Utilise tes secrets; fallback = préprod (comme ton code qui marche)
                    return (st.secrets["crm"].get("base_url", "https://preprod.api-concierge.mybackoffice.fr")).rstrip("/")

                @st.cache_data(show_spinner=False, ttl=1800)
                def _crm_login_prod() -> tuple[str | None, str | None]:
                    base = _crm_base_url()
                    auth_url = f"{base}/api/appMember/concierge/login"
                    payload = {"email": st.secrets["crm"]["email"], "password": st.secrets["crm"]["password"]}

                    try:
                        r = requests.post(auth_url, json=payload, timeout=30)
                    except requests.RequestException as e:
                        st.error("❌ Échec réseau (auth).")
                        with st.expander("Détails réseau (auth)"):
                            st.write({"auth_url": auth_url, "error": str(e)})
                        return None, None

                    ctype = (r.headers.get("content-type") or "").lower()
                    try:
                        body = r.json() if "application/json" in ctype else r.text
                    except ValueError:
                        body = r.text

                    if r.status_code != 200 or not isinstance(body, dict) or not body.get("success"):
                        st.error(f"❌ Auth KO (HTTP {r.status_code}).")
                        with st.expander("Détails réponse (auth)"):
                            st.write({"auth_url": auth_url, "status": r.status_code, "body": body})
                        return None, None

                    concierge_hash = str(body.get("ConciergeHash", "")).strip()
                    api_token     = str(body.get("ApiToken", "")).strip()
                    if not concierge_hash or not api_token:
                        st.error("❌ Auth KO (Hash/Token manquants).")
                        with st.expander("Détails réponse (auth)"):
                            st.write({"auth_url": auth_url, "status": r.status_code, "body": body})
                        return None, None

                    return concierge_hash, api_token

                def push_compte_tiers_to_crm(invoice_number: str, value: str, timeout: float = 15.0):
                    """
                    POST /api/myagency/controller/accounting/{ConciergeHash}
                    Header: ApiToken
                    + Ajoute 'date' (YYYY-MM-DD) si Payment Date est dispo dans df_source_encaissements
                    """
                    concierge_hash, api_token = _crm_login_prod()
                    if not concierge_hash or not api_token:
                        return None, "auth_failed"

                    url = f"{_crm_base_url()}/api/myagency/controller/accounting/{concierge_hash}"
                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "ApiToken": api_token,
                    }

                    # Récupère la date de paiement depuis la source, selon 'Invoice #'
                    iso_date = None
                    try:
                        src = st.session_state.get("df_source_encaissements")
                        if src is not None and "Payment Date" in src.columns:
                            sub = src[src["Invoice #"].astype(str).str.strip() == str(invoice_number).strip()]
                            if not sub.empty:
                                iso_date = _to_iso_date(sub.iloc[0]["Payment Date"])
                    except Exception:
                        iso_date = None

                    payload = {
                        "payload": {
                            "InvoiceNumber": str(invoice_number).strip(),
                            "type": "member",
                            "field": "vente",
                            "value": str(value).strip(),
                        }
                    }
                    if iso_date:  # n’ajoute la date que si elle est valide
                        payload["payload"]["date"] = iso_date

                    try:
                        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
                        ctype = (resp.headers.get("content-type") or "").lower()
                        body = resp.json() if "application/json" in ctype else resp.text
                        return resp.status_code, body
                    except requests.RequestException as e:
                        return None, f"Request error: {e}"
                # ==== ⬆️ FIN API corrigée ⬆️ ====

                # ==== ⬆️ FIN remplacement API encaissements ⬆️ ====

                # Tableau éditable des paires (Name, Account Global) uniques
                unique_names = (
                    df_errors
                    .sort_values("Invoice #")  # facultatif : assure quel "premier" tu veux garder
                    .drop_duplicates(subset=["Name", "Account Global"], keep="first")
                    [["Name", "Account Global", "Invoice #"]]
                )

                edited_df = st.data_editor(unique_names, key="corrections", hide_index=True)

                # Validation des corrections → applique sur df_source_encaissements
                if st.button("✅ Valider les corrections"):
                    df_to_update = st.session_state["df_source_encaissements"]
                    for _, row in edited_df.iterrows():
                        old_name = row['Name']
                        new_ag = row['Account Global']
                        df_to_update.loc[(df_to_update['Name'] == old_name) & (df_to_update['Account Client'] == 411000), 'Account Global'] = new_ag

                    # Mise à jour de la source et préparation relance
                    st.session_state["df_source_encaissements"] = df_to_update
                    st.session_state["modifs_validees"] = True
                    st.session_state.pop("controle_logs", None)  # supprimer anciens logs avant relance

                    # 2) PUSH des modifs vers le CRM pour chaque facture éditée
                    api_logs = []
                    with st.spinner("Mise à jour des comptes tiers dans le CRM..."):
                        for _, row in edited_df.iterrows():
                            invoice_number = str(row["Invoice #"]).strip()
                            compte_value = str(row["Account Global"]).strip()
                            # même normalisation que local
                            if compte_value == "411":
                                compte_value = "411-NO MEMBER ACCOUNT"

                            # skip si facture vide
                            if not invoice_number:
                                api_logs.append(f"⚠️ Facture sans numéro — ligne ignorée.")
                                continue

                            status, body = push_compte_tiers_to_crm(invoice_number, compte_value)
                            if status and 200 <= status < 300:
                                api_logs.append(f"✅ CRM ok — Facture {invoice_number} → {compte_value} (HTTP {status})")
                            else:
                                api_logs.append(f"❌ CRM ko — Facture {invoice_number} → {compte_value} (HTTP {status}) | {body}")

                    with st.expander("Détails des mises à jour CRM"):
                        for line in api_logs:
                            st.write(line)


                    st.success("✅ Modifications enregistrées. Clique sur « Relancer le contrôle ».")

                # Bouton de relance visible uniquement après validation
                if st.session_state["modifs_validees"]:
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("🔁 Relancer le contrôle"):
                            # Purge et recalcul au prochain passage
                            st.session_state.pop("controle_logs", None)
                            st.session_state["modifs_validees"] = False
                            st.rerun()

                    # Optionnel : si après validation il n’y a plus d’erreurs, proposer export (sera géré après relance)
                    with col2:
                        st.info("Après relance, si tout est OK, un bouton d’export apparaîtra ici automatiquement.")
    else:
        if uploaded_file is None:
            st.info("Importe un fichier pour lancer le contrôle.")

# Optionnel : exécution directe locale
if __name__ == "__main__":
    run_encaissements()
