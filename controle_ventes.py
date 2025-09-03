import streamlit as st
import pandas as pd
from io import BytesIO, StringIO
from controle_ventes_logic import run_ventes_checks_console
from xlsx2csv import Xlsx2csv

# ✅ Lecture robuste de fichier Excel
def safe_read_excel(uploaded, header_row: int = 2) -> pd.DataFrame:
    try:
        return pd.read_excel(uploaded, header=header_row, engine="openpyxl")
    except Exception:
        uploaded.seek(0)
        csv_buffer = StringIO()
        Xlsx2csv(BytesIO(uploaded.read()), outputencoding="utf-8", startrow=header_row + 1).convert(csv_buffer)
        csv_buffer.seek(0)
        return pd.read_csv(csv_buffer, header=0)

# ✅ Conversion pour téléchargement Excel
def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

# ✅ Interface principale
def afficher_interface(df: pd.DataFrame, force_recontrole=False):
    if "modifs_validees" not in st.session_state:
        st.session_state["modifs_validees"] = False

    if force_recontrole or "controle_logs" not in st.session_state:
        logs, factures_ko, nb_ko, df_checked = run_ventes_checks_console(df.copy())
        st.session_state["controle_logs"] = {
            "logs": logs,
            "factures_ko": factures_ko,
            "nb_ko": nb_ko,
            "df": df_checked
        }
        st.session_state["df_source_ventes"] = df_checked
    else:
        logs = st.session_state["controle_logs"]["logs"]
        factures_ko = st.session_state["controle_logs"]["factures_ko"]
        nb_ko = st.session_state["controle_logs"]["nb_ko"]
        df_checked = st.session_state["controle_logs"]["df"]

    # 📋 Affichage des logs
    st.subheader("📝 Logs")
    st.code("\n".join(st.session_state["controle_logs"]["logs"]), language="text")

    # ⚠️ Factures KO
    if nb_ko:
        st.warning("Des ventes KO subsistent. Modifie les tableaux puis clique sur « Valider les corrections ».")
        st.markdown("### ✏️ Modifie les comptes tiers ci-dessous")

        df_ko = df_checked[df_checked["Numéro de facture"].isin(factures_ko)].copy()
        df_ko = df_ko.drop_duplicates(subset="Numéro de facture").copy()
        df_ko["Prénom et Nom"] = df_ko["Nom client + service"].astype(str).str.split("-").str[0].str.strip()
        df_ko["Compte tiers"] = "411"
        df_ko = df_ko.drop_duplicates(subset="Prénom et Nom")

        import requests

        # --- Config API (à mettre en haut du fichier si tu préfères) ---
        API_URL = "https://preprod.api-concierge.mybackoffice.fr/api/myagency/controller/accounting"
        API_HEADERS = {
            "Content-Type": "application/json",
            # "Authorization": "Bearer <token>"  # si besoin
        }

        # --- Helper pour envoyer la maj au CRM ---
        def push_compte_tiers_to_crm(invoice_number: str, value: str, timeout: float = 15.0):
            payload = {
                "payload": {
                    "InvoiceNumber": str(invoice_number).strip(),
                    "type": "member",   # client
                    "field": "vente",   # compte client 411
                    "value": str(value).strip()
                }
            }
            try:
                resp = requests.post(API_URL, json=payload, headers=API_HEADERS, timeout=timeout)
                ctype = (resp.headers.get("content-type") or "").lower()
                body = resp.json() if "application/json" in ctype else resp.text
                return resp.status_code, body
            except requests.RequestException as e:
                return None, f"Request error: {e}"

        # -----------------------------------------------------------------------------------
        # Ton UI existante + l'appel API par ligne
        edited_df = st.data_editor(
            df_ko[["Prénom et Nom", "Compte tiers", "Numéro de facture"]],
            key="factures_ko_global",
            hide_index=False,
        )

        # ✅ Application des corrections
        if st.button("✅ Valider les corrections"):
            mapping_nom_to_compte = {
                nom: ("411-NO MEMBER ACCOUNT" if compte.strip() == "411" else compte.strip())
                for nom, compte in zip(edited_df["Prénom et Nom"], edited_df["Compte tiers"])
            }

            for nom, compte in mapping_nom_to_compte.items():
                mask = (
                    df["Nom client + service"].astype(str).str.startswith(nom)
                    & (df["Compte général"].astype(str).str.strip() == "411000")
                )
                df.loc[mask, "Compte tiers"] = compte

            st.session_state["df_source_ventes"] = df
            st.session_state.pop("controle_logs", None)  # supprimer anciens logs
            st.session_state["modifs_validees"] = True

            # 2) PUSH des modifs vers le CRM pour chaque facture éditée
            api_logs = []
            with st.spinner("Mise à jour des comptes tiers dans le CRM..."):
                for _, row in edited_df.iterrows():
                    invoice_number = str(row["Numéro de facture"]).strip()
                    compte_value = str(row["Compte tiers"]).strip()
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
            st.success("✅ Modifications enregistrées. Clique sur le bouton ci-dessous pour relancer le contrôle.")

        # ✅ Affichage conditionnel des boutons après validation
        if st.session_state["modifs_validees"]:
            col1, col2 = st.columns(2)

            with col1:
                if st.button("🔁 Relancer le contrôle"):
                    st.session_state.pop("controle_logs", None)
                    st.session_state["modifs_validees"] = False
                    afficher_interface(st.session_state["df_source_ventes"], force_recontrole=True)
                    st.stop()

            with col2:
                if "controle_logs" in st.session_state and not st.session_state["controle_logs"]["factures_ko"]:
                    buf = dataframe_to_excel_bytes(st.session_state["df_source_ventes"])
                    st.download_button(
                        "📥 Télécharger le fichier corrigé",
                        buf,
                        "ventes_corrigées.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

    else:
        # ✅ Tout est OK
        st.success("🎉 Plus aucune vente KO. Tu peux exporter le fichier corrigé.")
        buf = dataframe_to_excel_bytes(df_checked)
        st.download_button(
            "📥 Télécharger le fichier corrigé",
            buf,
            "ventes_corrigées.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

# ▶️ Logique de lancement
def run_interface():
    st.title("📈 Contrôle automatique des écritures de ventes")

    if "df_source_ventes" not in st.session_state:
        uploaded = st.file_uploader("Importe ton fichier Excel des ventes", type=["xlsx"], key="uploader_ventes")
        if uploaded:
            df = safe_read_excel(uploaded, header_row=2)
            st.session_state["df_source_ventes"] = df
            afficher_interface(df, force_recontrole=True)
    else:
        afficher_interface(st.session_state["df_source_ventes"])

run_interface()
