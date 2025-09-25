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

def apply_cb_to_amex_fix(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    for inv, g in out.groupby("Invoice #"):
        dsum = round(float(g["Debit"].sum()), 2)
        csum = round(float(g["Credit"].sum()), 2)
        if dsum == csum:
            continue  # déjà équilibré

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

        # 1) CB -> AM (colonne index 0)
        out.loc[idxs, first_col] = "AM"

        # 2) 5112XX -> 5113XX
        def _map_5112_to_5113(x):
            s = str(x).strip()
            s = re.sub(r"\.0$", "", s)
            m = re.fullmatch(r"5112(\d{2})", s)
            return f"5113{m.group(1)}" if m else x

        out.loc[idxs, "Account Global"] = out.loc[idxs, "Account Global"].apply(_map_5112_to_5113)

        # 2bis) aligner aussi Payment Mean -> AMEX pour cohérence de la règle B
        if "Payment Mean" in out.columns:
            out.loc[idxs, "Payment Mean"] = "AMEX"

        # 3) ajouter la commission au 'Debit'
        ag_invoice = out.loc[idxs, "Account Global"]
        blank_mask = ag_invoice.isna() | (ag_invoice.astype(str).str.strip() == "")
        if blank_mask.any():
            target_idx = ag_invoice[blank_mask].index[0]
        else:
            cand_411 = out.loc[idxs][out.loc[idxs, "Account Client"] == 411000].index
            if len(cand_411) > 0:
                target_idx = cand_411[0]
            else:
                cand_nocredit = out.loc[idxs][pd.to_numeric(out.loc[idxs, "Credit"], errors="coerce").fillna(0) == 0].index
                target_idx = cand_nocredit[0] if len(cand_nocredit) > 0 else idxs[0]

        cur = pd.to_numeric(out.loc[target_idx, "Debit"], errors="coerce")
        if pd.isna(cur):
            cur = 0.0
        out.loc[target_idx, "Debit"] = round(float(cur) + credit_627, 2)

    return out


def check_invoices(df):
    logs = []
    error = False

    # ✅ appliquer la correction AMEX avant de contrôler
    corrected_df = apply_cb_to_amex_fix(df.copy())

    invoices = df['Invoice #'].unique()
    for inv in invoices:
        sub_orig = df[df['Invoice #'] == inv]
        sub      = corrected_df[corrected_df['Invoice #'] == inv]

        debit_sum_orig  = round(sub_orig['Debit'].sum(), 2)
        credit_sum_orig = round(sub_orig['Credit'].sum(), 2)
        debit_sum       = round(sub['Debit'].sum(), 2)
        credit_sum      = round(sub['Credit'].sum(), 2)

        sublogs = []
        is_ok = True

        # A) équilibre après correction ?
        corrected = (debit_sum_orig != credit_sum_orig) and (debit_sum == credit_sum)
        if debit_sum != credit_sum:
            sublogs.append(f"❌ Invoice {inv} : Debit ≠ Credit ({debit_sum} ≠ {credit_sum})")
            is_ok = False

        # B) règle format Account Global vs Payment Mean/mois (sur DF corrigé)
        try:
            payment_mean = sub['Payment Mean'].iloc[0].upper()
            second_row = sub.iloc[1]
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

        # C) 411000 vs '411-NO MEMBER ACCOUNT' (sur DF corrigé)
        for _, row in sub.iterrows():
            if row['Account Client'] == 411000 and row['Account Global'] == "411-NO MEMBER ACCOUNT":
                sublogs.append(f"❌ Invoice {inv} : Account Global is '411-NO MEMBER ACCOUNT' for 411000 client")
                is_ok = False
                break

        if is_ok:
            if corrected:
                logs.append(f"🛠️ Invoice {inv} : auto-correction AMEX appliquée (CB→AM, 5112xx→5113xx, commission ajoutée).")
            else:
                logs.append(f"✅ Invoice {inv} : OK")
        else:
            logs.extend(sublogs)
            error = True

    return logs, error, corrected_df


def transform_for_download(df):
    logs = []
    df = df.copy()

    # 1) Échanger les valeurs entre 'Date' et 'Payment Date'
    df[['Date', 'Payment Date']] = df[['Payment Date', 'Date']]
    logs.append("🔁 Colonnes 'Date' et 'Payment Date' échangées.")

    # 2) Si Account Client = 411000, échanger avec Account Global
    mask = df['Account Client'] == 411000
    df.loc[mask, ['Account Client', 'Account Global']] = df.loc[mask, ['Account Global', 'Account Client']].values
    logs.append("🔁 Inversion 'Account Client' et 'Account Global' pour les lignes 411000.")

    # 3) Supprimer 'Payment Mean', 'Date', 'Comment'
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

    uploaded_file = st.file_uploader("📤 Upload ton fichier Excel (format tableau)", type=["xlsx", "xls", "csv"], key="uploader_encaissements_v2")

    # Init des flags d'état
    if "modifs_validees" not in st.session_state:
        st.session_state["modifs_validees"] = False
    if "df_source_encaissements" not in st.session_state and uploaded_file is not None:
        df_init = safe_read_excel(uploaded_file, header_row=1)
        # Nettoyage de la colonne Name
        df_init['Name'] = df_init['Name'].astype(str).apply(clean_name)
        st.success("🧽 Colonne 'Name' nettoyée (conservation avant le '-').")
        st.session_state["df_source_encaissements"] = df_init.copy()

    # Si on a une source, on affiche/contrôle
    if "df_source_encaissements" in st.session_state:
        df_current = st.session_state["df_source_encaissements"]

        # Recalcul des logs si nécessaire (ou première fois)
        if "controle_logs" not in st.session_state:
            logs, has_errors, df_after = check_invoices(df_current.copy())
            st.session_state["controle_logs"] = {
                "logs": logs,
                "has_errors": has_errors,
                "df": df_after.copy()
            }
        else:
            logs = st.session_state["controle_logs"]["logs"]
            has_errors = st.session_state["controle_logs"]["has_errors"]

        # Affichage des logs
        st.subheader("🧾 Résultats des vérifications")
        for log in st.session_state["controle_logs"]["logs"]:
            st.markdown(log)

        # Cas sans erreurs → export direct
        if not st.session_state["controle_logs"]["has_errors"]:
            df_export, export_logs = transform_for_download(st.session_state["controle_logs"]["df"].copy())
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
            df_errors = df_checked[df_checked['Account Global'] == "411-NO MEMBER ACCOUNT"]

            # 🔄 Si aucun enregistrement à corriger, on exporte directement le DF corrigé
            if df_errors.empty:
                st.success("🎉 Aucune ligne avec '411-NO MEMBER ACCOUNT' à corriger.")
                df_export, export_logs = transform_for_download(st.session_state["controle_logs"]["df"].copy())

                buf = dataframe_to_excel_bytes(df_export)
                st.download_button(
                    "📤 Exporter le fichier excel corrigé",
                    data=buf,
                    file_name="encaissements_corrigés.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                for l in export_logs:
                    st.info(l)

            else:
                st.warning("⚠️ Il reste des lignes avec '411-NO MEMBER ACCOUNT'. Corrige-les ci-dessous.")

                # ==== ⬇️ API CRM (inchangée) ⬇️ ====
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
                    api_token = str(body.get("ApiToken", "")).strip()
                    if not concierge_hash or not api_token:
                        st.error("❌ Auth KO (Hash/Token manquants).")
                        with st.expander("Détails réponse (auth)"):
                            st.write({"auth_url": auth_url, "status": r.status_code, "body": body})
                        return None, None
                    return concierge_hash, api_token

                def push_compte_tiers_to_crm(invoice_number: str, value: str, timeout: float = 15.0):
                    concierge_hash, api_token = _crm_login_prod()
                    if not concierge_hash or not api_token:
                        return None, "auth_failed"
                    url = f"{_crm_base_url()}/api/myagency/controller/accounting/{concierge_hash}"
                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "ApiToken": api_token,
                    }
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
                    if iso_date:
                        payload["payload"]["date"] = iso_date
                    try:
                        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
                        ctype = (resp.headers.get("content-type") or "").lower()
                        body = resp.json() if "application/json" in ctype else resp.text
                        return resp.status_code, body
                    except requests.RequestException as e:
                        return None, f"Request error: {e}"
                # ==== ⬆️ FIN API CRM (inchangée) ⬆️ ====

                # Tableau éditable des paires (Name, Account Global) uniques
                unique_names = (
                    df_errors
                    .sort_values("Invoice #")
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
                    st.session_state.pop("controle_logs", None)

                    # PUSH vers le CRM pour chaque facture éditée
                    api_logs = []
                    with st.spinner("Mise à jour des comptes tiers dans le CRM..."):
                        for _, row in edited_df.iterrows():
                            invoice_number = str(row["Invoice #"]).strip()
                            compte_value = str(row["Account Global"]).strip()
                            if compte_value == "411":
                                compte_value = "411-NO MEMBER ACCOUNT"
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

                if st.session_state["modifs_validees"]:
                    col1, col2 = st.columns(2)
                    with col1:
                        if st.button("🔁 Relancer le contrôle"):
                            st.session_state.pop("controle_logs", None)
                            st.session_state["modifs_validees"] = False
                            st.rerun()
                    with col2:
                        st.info("Après relance, si tout est OK, un bouton d’export apparaîtra ici automatiquement.")
    else:
        if uploaded_file is None:
            st.info("Importe un fichier pour lancer le contrôle.")

# Optionnel : exécution directe locale
if __name__ == "__main__":
    run_encaissements()
