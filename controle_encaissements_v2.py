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

def transform_for_download(df):
    logs = []
    df = df.copy()
    
    # 1. Échanger colonne J et B (Payment Mean avec Account Global)
    df[['Account Global', 'Payment Mean']] = df[['Payment Mean', 'Account Global']]
    logs.append("🔁 Colonnes 'Account Global' et 'Payment Mean' échangées.")

    # 2. Si Account Client = 411000, échanger avec Account Global
    mask = df['Account Client'] == 411000
    df.loc[mask, ['Account Client', 'Account Global']] = df.loc[mask, ['Account Global', 'Account Client']].values
    logs.append("🔁 Inversion 'Account Client' et 'Account Global' pour les lignes 411000.")

    # 3. Supprimer les colonnes J,K,L (Payment Mean, Payment Date, Comment)
    df.drop(columns=['Payment Mean', 'Payment Date', 'Comment'], inplace=True)
    logs.append("🗑️ Colonnes 'Payment Mean', 'Payment Date', 'Comment' supprimées.")

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
            df_export, export_logs = transform_for_download(st.session_state["controle_logs"]["df"])
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

            # 🔄 NOUVEAU: si aucun enregistrement à corriger, ne pas afficher l'alerte ni l'éditeur
            if df_errors.empty:
                st.success("🎉 Aucune ligne avec '411-NO MEMBER ACCOUNT' à corriger.")

                # Préparer l'export avec les 3 modifications
                df_export = st.session_state["df_source_encaissements"].copy()

                # 1) Échanger les valeurs entre les colonnes par position (index 1 et 10)
                if df_export.shape[1] > 10:
                    df_export.iloc[:, [1, 10]] = df_export.iloc[:, [10, 1]].to_numpy()

                # 2) Lorsque Account Client = 411000, inverser Account Client et Account Global (échange de valeurs)
                if "Account Client" in df_export.columns and "Account Global" in df_export.columns:
                    mask_swap = df_export["Account Client"] == 411000
                    df_export.loc[mask_swap, ["Account Client", "Account Global"]] = df_export.loc[mask_swap, ["Account Global", "Account Client"]].values

                # 3) Supprimer les 3 colonnes demandées : Payment Mean, Payment Date, Comment (si présentes)
                cols_to_drop = [c for c in ["Payment Mean", "Payment Date", "Comment"] if c in df_export.columns]
                if cols_to_drop:
                    df_export.drop(columns=cols_to_drop, inplace=True)

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

                # Tableau éditable des paires (Name, Account Global) uniques
                unique_names = df_errors[['Name', 'Account Global','Invoice #']].drop_duplicates(keep='first')
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
