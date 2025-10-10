import streamlit as st
import pandas as pd
from io import BytesIO, StringIO
import re, sys
from typing import List, Tuple
from controle_achats_logic import run_checks

# ── AJOUT: helpers/API pour push CRM ──────────────────────────────────────────
import requests

API_URL = "https://preprod.api-concierge.mybackoffice.fr/api/myagency/controller/accounting"
API_HEADERS = {"Content-Type": "application/json"}  # + Authorization si besoin

def _to_date_iso(d) -> str:
    if pd.isna(d):
        return ""
    if isinstance(d, pd.Timestamp):
        return d.strftime("%Y-%m-%d")
    s = str(d).strip()
    try:
        return pd.to_datetime(s, dayfirst=True, errors="raise").strftime("%Y-%m-%d")
    except Exception:
        try:
            return pd.to_datetime(s, errors="raise").strftime("%Y-%m-%d")
        except Exception:
            return ""

def build_invoice_number_from_piece_and_date(piece: str, date_facture) -> str:
    date_iso = _to_date_iso(date_facture)
    try:
        month = pd.to_datetime(date_iso).month if date_iso else 0
    except Exception:
        month = 0
    return f"{month:02d}-{str(piece).strip()}"

def push_compte_tiers_to_crm(invoice_number: str, value: str, date_iso: str, timeout: float = 15.0):
    payload = {
        "payload": {
            "InvoiceNumber": str(invoice_number),
            "type": "member",
            "field": "vente",   # conforme à ta spec même côté achats
            "value": str(value),
            "date": str(date_iso)
        }
    }
    try:
        resp = requests.post(API_URL, json=payload, headers=API_HEADERS, timeout=timeout)
        ctype = (resp.headers.get("content-type") or "").lower()
        body = resp.json() if "application/json" in ctype else resp.text
        return resp.status_code, body
    except requests.RequestException as e:
        return None, f"Request error: {e}"
# ──────────────────────────────────────────────────────────────────────────────

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
    with st.sidebar:
        st.markdown("### 📅 Export permanent")
        st.download_button(
        "📅 Télécharger maintenant",
        dataframe_to_excel_bytes(_get_df_to_export_anytime()),
        "encaissements_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="dl_sidebar_anytime"
        )


def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

def run_interface():
    st.title("📊 Contrôle automatique des écritures d'achats")

    show_sidebar_download() # bouton dispo tout le temps à gauche


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

                # ── AJOUT: push API vers CRM par n° de pièce ─────────────────────
                api_logs = []
                with st.spinner("Mise à jour des comptes tiers dans le CRM..."):
                    for _, r in edited.iterrows():
                        piece = str(r["n° de piece"]).strip()
                        mask_piece = (df["n° de piece"].astype(str).str.strip() == piece) & (df["Compte Généraux"] == "401000")
                        if not mask_piece.any():
                            api_logs.append(f"⚠️ Pièce {piece}: aucune ligne 401000 trouvée, ignorée.")
                            continue

                        date_facture_piece = df.loc[mask_piece, "Date Facture"].iloc[0] if "Date Facture" in df.columns else ""
                        date_iso = _to_date_iso(date_facture_piece)
                        inv_num = build_invoice_number_from_piece_and_date(piece, date_facture_piece)
                        compte_value = str(r.get("Compte Tiers", "")).strip()

                        if not inv_num or not date_iso or not compte_value:
                            api_logs.append(
                                f"⚠️ Pièce {piece}: infos incomplètes — InvoiceNumber='{inv_num}', date='{date_iso}', Compte='{compte_value}'"
                            )
                            continue

                        status, body = push_compte_tiers_to_crm(inv_num, compte_value, date_iso)
                        if status and 200 <= status < 300:
                            api_logs.append(f"✅ CRM ok — Pièce {piece} → {compte_value} | Invoice {inv_num} | date {date_iso} (HTTP {status})")
                        else:
                            api_logs.append(f"❌ CRM ko — Pièce {piece} → {compte_value} | Invoice {inv_num} | date {date_iso} (HTTP {status}) | {body}")

                with st.expander("Détails des mises à jour CRM"):
                    for line in api_logs:
                        st.write(line)
                # ────────────────────────────────────────────────────────────────

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
