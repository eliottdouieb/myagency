import streamlit as st
import pandas as pd
from io import BytesIO, StringIO
import sys
from controle_ventes_logic import run_ventes_checks_console


def safe_read_excel(uploaded, header_row: int = 2) -> pd.DataFrame:
    try:
        return pd.read_excel(uploaded, header=header_row, engine="openpyxl")
    except Exception as err:
        st.warning(f"openpyxl a échoué ; utilisation de xlsx2csv → {err}")
        from xlsx2csv import Xlsx2csv
        uploaded.seek(0)
        csv_buffer = StringIO()
        Xlsx2csv(BytesIO(uploaded.read()), outputencoding="utf-8", startrow=header_row + 1).convert(csv_buffer)
        csv_buffer.seek(0)
        return pd.read_csv(csv_buffer, header=0)


def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

def run_interface():
    st.title("📈 Contrôle automatique des écritures de ventes")

    uploaded = st.file_uploader("Importe ton fichier Excel des ventes", type=["xlsx"])

    if uploaded:
        if "df_source_ventes" not in st.session_state:
            st.session_state.df_source_ventes = safe_read_excel(uploaded, header_row=1)

        df = st.session_state.df_source_ventes.copy()

        logs, factures_ko, nb_ko, df = run_ventes_checks_console(df)

        # --------- Affichage logs ---------
        st.subheader("📝 Logs")
        st.code("\n".join(logs), language="text")

        if nb_ko:
            st.warning(
                "Des ventes KO subsistent. Modifie les tableaux puis clique sur « Valider les corrections »."
            )

            edited_factures = []
            for num in factures_ko:
                st.markdown(f"### ✏️ Facture {num}")
                facture_df = df[df["Numéro de facture"] == num].copy()

                # Fix possible valeurs nulles dans Concierge
                facture_df["Concierge"] = facture_df["Concierge"].fillna("")

                editable_cols = [
                    "Compte général", "Compte tiers", "Concierge", "Nom client + service",
                    "Débit", "Crédit", "Monnaie", "Analytique", "Code"
                ]

                # NE PAS cacher l’index ici
                edited = st.data_editor(
                    facture_df[["Numéro de facture"] + editable_cols],
                    key=f"facture_{num}",
                    hide_index=False,
                )

                edited_factures.append(edited)

            if st.button("✅ Valider les corrections"):
                for edited in edited_factures:
                    for idx, row in edited.iterrows():
                        df.loc[idx, row.index] = row

                st.session_state.df_source_ventes = df
                st.success("✅ Modifications enregistrées. Clique sur le bouton ci-dessous pour relancer le contrôle.")

                if st.button("🔁 Relancer le contrôle"):
                    st._is_running_with_streamlit = True
                    sys.exit()

        else:
            # --------- Sinon → export ---------
            st.success("🎉 Plus aucune vente KO. Tu peux exporter le fichier corrigé.")
            buf = dataframe_to_excel_bytes(df)
            st.download_button(
                "📥 Télécharger le fichier corrigé",
                buf,
                "ventes_corrigées.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

run_interface()
