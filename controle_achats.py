
import streamlit as st
import pandas as pd
from io import BytesIO, StringIO
import re, sys
from typing import List, Tuple
from controle_achats_logic import run_checks

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

def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

def run_interface():
    st.title("📊 Contrôle automatique des écritures d'achats")

    uploaded = st.file_uploader("Importe ton fichier Excel des achats", type=["xlsx"])

    if uploaded:
        if "df_source" not in st.session_state:
            st.session_state.df_source = safe_read_excel(uploaded, header_row=1)

        df = st.session_state.df_source.copy()

        logs, ko_pieces, nb_ko = run_checks(df)

        if nb_ko == 0 and "Concierge" in df.columns:
            df.drop(columns=["Concierge"], inplace=True)
            logs.append("✅ Colonne Concierge supprimée avant export.")

        st.subheader("📝 Logs")
        st.code("\n".join(logs), language="text")

        if nb_ko:
            st.warning(
                "Des achats KO subsistent (Compte Tiers invalide). "
                "Modifie le tableau puis clique sur « Valider les corrections »."
            )

            df_ko = df[
                (df["n° de piece"].isin(ko_pieces)) & (df["Compte Généraux"] == "401000")
            ].copy()

            # Ne garder qu'une ligne par libellé
            df_unique = df_ko.drop_duplicates(subset="Libelle")

            edited = st.data_editor(
                df_unique[
                    ["n° de piece", "Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge"]
                ],
                key="ko_editor",
                hide_index=True,
            )


            if st.button("✅ Valider les corrections"):
                for _, r in edited.iterrows():
                    idx = df[
                        (df["Libelle"] == r["Libelle"]) & (df["Compte Généraux"] == "401000")
                    ].index
                    if not idx.empty:
                        df.loc[
                            idx,
                            ["Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge"]
                        ] = r[
                            ["Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge"]
                        ].values

                st.session_state.df_source = df
                st.success("✅ Modifications enregistrées. Clique sur le bouton ci-dessous pour relancer le contrôle.")
                if st.button("🔁 Relancer le contrôle"):
                    st._is_running_with_streamlit = True
                    sys.exit()
        else:
            st.success("🎉 Plus aucun achat KO. Tu peux exporter le fichier corrigé.")
            buf = dataframe_to_excel_bytes(df)
            st.download_button(
                "📥 Télécharger le fichier corrigé",
                buf,
                "achats_corriges.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
