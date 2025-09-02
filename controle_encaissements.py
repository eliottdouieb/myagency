import streamlit as st
import pandas as pd
from io import BytesIO, StringIO
import re, sys
from typing import List, Tuple
from controle_encaissements_logic import run_checks, rerun_check_no_member_only

def drop_columns(df: pd.DataFrame, cols: List[str], logs: List[str]):
    df.drop(columns=cols, inplace=True, errors="ignore")
    logs.append(f"✅ Colonnes {', '.join(cols)} supprimées.")

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

def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

def run_interface():
    st.title("📊 Contrôle automatique des encaissements")

    uploaded = st.file_uploader("Importe ton fichier Excel des encaissements", type=["xlsx"])

    if uploaded:
        if "df_encaissements" not in st.session_state:
            st.session_state.df_encaissements = safe_read_excel(uploaded, header_row=1)
            df = st.session_state.df_encaissements.copy()
            logs, ko_invoices, nb_ko = run_checks(df)
            st.session_state["controle_logs"] = logs
        else:
            df = st.session_state.df_encaissements.copy()

        # Affichage des logs
        st.subheader("📝 Logs")
        st.code("\n".join(st.session_state["controle_logs"]), language="text")

        # 🔎 Détection des lignes avec '411-NO MEMBER ACCOUNT'
        mask_411_no_member = (
            (df["Account Global"].astype(str).str.strip() == "411000") &
            (df["Account Client"].astype(str).str.strip().str.upper() == "411-NO MEMBER ACCOUNT")
        )
        df_no_member = df[mask_411_no_member].copy()

        if not df_no_member.empty:
            st.warning("Des lignes contiennent '411-NO MEMBER ACCOUNT'. Modifie-les ci-dessous 👇")

            df_editable = (
                df_no_member[["Invoice #", "Account Global", "Account Client", "Name"]]
                .sort_values("Invoice #")
                .drop_duplicates(subset="Name", keep="first")
            )

            edited = st.data_editor(
                df_editable,
                key="editor_no_member",
                hide_index=True,
                use_container_width=True,
            )

            if st.button("✅ Valider les modifications"):
                for _, row in edited.iterrows():
                    invoice = row["Invoice #"]
                    new_account_client = row["Account Client"]
                    mask_to_update = (
                        (df["Invoice #"] == invoice) &
                        (df["Account Global"].astype(str).str.strip() == "411000")
                    )
                    df.loc[mask_to_update, "Account Client"] = new_account_client

                st.session_state.df_encaissements = df
                st.success("✅ Modifications enregistrées. Tu peux maintenant relancer le contrôle.")

            if st.button("🔁 Relancer le contrôle des 'NO MEMBER ACCOUNT' uniquement"):
                df_updated = st.session_state.df_encaissements.copy()

                # 🔁 Mise à jour partielle des logs
                updated_logs, ko_invoices, nb_ko = rerun_check_no_member_only(
                    df_updated, st.session_state["controle_logs"]
                )
                st.session_state["controle_logs"] = updated_logs

                # Réaffichage des logs mis à jour
                st.subheader("📝 Logs après relance partielle")
                st.code("\n".join(updated_logs), language="text")

                # Vérifie s’il reste des 'NO MEMBER ACCOUNT'
                mask_411_no_member = (
                    (df_updated["Account Global"].astype(str).str.strip() == "411000") &
                    (df_updated["Account Client"].astype(str).str.strip().str.upper() == "411-NO MEMBER ACCOUNT")
                )
                df_no_member = df_updated[mask_411_no_member].copy()

                if not df_no_member.empty:
                    st.warning("Encore des '411-NO MEMBER ACCOUNT'. Modifie-les ci-dessous 👇")

                    df_editable = (
                        df_no_member[["Invoice #", "Account Global", "Account Client", "Name"]]
                        .sort_values("Invoice #")
                        .drop_duplicates(subset="Name", keep="first")
                    )

                    edited = st.data_editor(
                        df_editable,
                        key="editor_no_member_rerun",
                        hide_index=True,
                        use_container_width=True,
                    )

                    if st.button("✅ Valider les modifications (relance)"):
                        for _, row in edited.iterrows():
                            invoice = row["Invoice #"]
                            new_account_client = row["Account Client"]

                            st.session_state.df_encaissements.loc[
                                (st.session_state.df_encaissements["Invoice #"] == invoice) &
                                (st.session_state.df_encaissements["Account Global"].astype(str).str.strip() == "411000"),
                                "Account Client"
                            ] = new_account_client

                        st.success("✅ Modifications enregistrées. Tu peux relancer le contrôle.")
                else:
                    st.success("✅ Plus aucun '411-NO MEMBER ACCOUNT' détecté.")

        else:
            st.success("🎉 Aucune ligne '411-NO MEMBER ACCOUNT'. Tu peux exporter le fichier.")
            if st.button("📥 Télécharger le fichier corrigé"):
                df_to_export = df.copy()

                # 🧠 Extraction sécurisée des logs
                current_logs = st.session_state["controle_logs"]
                if isinstance(current_logs, dict):
                    current_logs = current_logs.get("logs", [])
                elif isinstance(current_logs, str):
                    current_logs = [current_logs]

                drop_columns(df_to_export, ["Analytics", "Payment Mean", "Payment Date", "Comment"], current_logs)

                buf = dataframe_to_excel_bytes(df_to_export)
                st.download_button(
                    "📥 Télécharger le fichier corrigé",
                    buf,
                    "encaissements_corriges.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
