import streamlit as st
import pandas as pd
from io import BytesIO, StringIO
from controle_ventes_logic import run_ventes_checks_console
from xlsx2csv import Xlsx2csv

# ✅ Lecture robuste de fichier Exce

# ─── Logger léger ──────────────────────────────────────────────────────────────
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


# ✅ Conversion pour téléchargement Excel
def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

def _get_invoice_date_from_source(invoice_number: str):
    src = st.session_state["df_source_ventes"]
    inv = str(invoice_number).strip()
    for col in ("Date", "Date Facture", "Payment Date"):
        if col in src.columns:
            sub = src[src["#"].astype(str).str.strip() == inv]
            if not sub.empty:
                return sub.iloc[0][col]
    return None


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

        df_ko = df_checked[df_checked["#"].isin(factures_ko)].copy()
        df_ko = df_ko.drop_duplicates(subset="#").copy()
        df_ko["Prénom et Nom"] = df_ko["Name"].astype(str).str.split("-").str[0].str.strip()
        df_ko["Account Client"] = "411"
        df_ko = df_ko.drop_duplicates(subset="Prénom et Nom")
        df_ko["Date"] = df_ko["#"].apply(_get_invoice_date_from_source)

        # ==== ⬇️ REMPLACE TOUT CE BLOC API PAR CELUI-CI (PROD + secrets) ⬇️ ====
        import requests
        from datetime import datetime, date


        def _to_iso_date(v) -> str | None:
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            if isinstance(v, (datetime, date, pd.Timestamp)):
                return pd.to_datetime(v).strftime("%Y-%m-%d")
            s = str(v).strip()
            # essais directs
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    pass
            # essai pandas (dayfirst pour '27/06/2025')
            try:
                return pd.to_datetime(s, dayfirst=True, errors="raise").strftime("%Y-%m-%d")
            except Exception:
                # sérial Excel éventuel
                try:
                    return pd.to_datetime(float(s), unit="D", origin="1899-12-30").strftime("%Y-%m-%d")
                except Exception:
                    return None

        def _crm_base_url() -> str:
            # Lit la PROD depuis secrets, fallback sur l’URL officielle PROD
            return (st.secrets["crm"].get("base_url", "https://preprod.api-concierge.mybackoffice.fr")).rstrip("/")

        @st.cache_data(show_spinner=False, ttl=1800)  # cache le login ~30 min
        def _crm_login_prod() -> tuple[str, str]:
            """
            Login PROD → retourne (ConciergeHash, ApiToken)
            """
            auth_url = f"{_crm_base_url()}/api/appMember/concierge/login"
            payload = {
                "email": st.secrets["crm"]["email"],
                "password": st.secrets["crm"]["password"],
            }
            r = requests.post(auth_url, json=payload, timeout=30)
            r.raise_for_status()
            data = r.json()
            if not data.get("success"):
                raise RuntimeError(f"Login failed: {data}")
            concierge_hash = str(data.get("ConciergeHash", "")).strip()
            api_token     = str(data.get("ApiToken", "")).strip()
            if not concierge_hash or not api_token:
                raise RuntimeError("Missing ConciergeHash or ApiToken in login response.")
            return concierge_hash, api_token

        def push_compte_tiers_to_crm(invoice_number: str,date_1: str, value: str, timeout: float = 15.0):
            """
            Envoie la mise à jour en PROD :
            POST /api/myagency/controller/accounting/{ConciergeHash}
            Header: ApiToken
            """
            concierge_hash, api_token = _crm_login_prod()

            url = f"{_crm_base_url()}/api/myagency/controller/accounting/{concierge_hash}"
            headers = {
                "Content-Type": "application/json",
                "ApiToken": api_token,
            }
            payload = {
                "payload": {
                    "InvoiceNumber": str(invoice_number).strip(),
                    "type": "member",   # client
                    "field": "vente",   # compte client 411
                    "value": str(value).strip(),
                    "date": _to_iso_date(date_1),  # décommente si tu dois envoyer une date côté ventes
                }
            }
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
                ctype = (resp.headers.get("content-type") or "").lower()
                body = resp.json() if "application/json" in ctype else resp.text
                return resp.status_code, body, resp.text
            except requests.RequestException as e:
                return None, f"Request error: {e}"
        # ==== ⬆️ FIN DU BLOC REMPLACÉ ⬆️ ====


        # -----------------------------------------------------------------------------------
        # Ton UI existante + l'appel API par ligne
        edited_df = st.data_editor(
            df_ko[["Prénom et Nom", "Account Client", "#", "Date"]],
            key="factures_ko_global",
            hide_index=False,
        )


        # ✅ Application des corrections
        if st.button("✅ Valider les corrections"):
            mapping_nom_to_compte = {
                nom: ("411-NO MEMBER ACCOUNT" if compte.strip() == "411" else compte.strip())
                for nom, compte in zip(edited_df["Prénom et Nom"], edited_df["Account Client"])
            }

            for nom, compte in mapping_nom_to_compte.items():
                mask = (
                    df["Name"].astype(str).str.startswith(nom)
                    & (df["Account General"].astype(str).str.strip() == "411000")
                )
                df.loc[mask, "Account Client"] = compte

            st.session_state["df_source_ventes"] = df
            st.session_state.pop("controle_logs", None)  # supprimer anciens logs
            st.session_state["modifs_validees"] = True

            # 2) PUSH des modifs vers le CRM pour chaque facture éditée
            api_logs = []
            with st.spinner("Mise à jour des comptes tiers dans le CRM..."):
                for _, row in edited_df.iterrows():
                    invoice_number = str(row["#"]).strip()
                    date_1 = str(row["Date"]).strip()
                    compte_value = str(row["Account Client"]).strip()
                    # même normalisation que local
                    if compte_value == "411":
                        compte_value = "411-NO MEMBER ACCOUNT"

                    # skip si facture vide
                    if not invoice_number:
                        api_logs.append(f"⚠️ Facture sans numéro — ligne ignorée.")
                        continue

                    status, body, text_1 = push_compte_tiers_to_crm(invoice_number,date_1, compte_value)
                    if status and 200 <= status < 300:
                        api_logs.append(f"✅ CRM ok — Facture {invoice_number} → {compte_value} (HTTP {status}), {text_1}")
                    else:
                        api_logs.append(f"❌ CRM ko — Facture {invoice_number} → {compte_value} (HTTP {status}) | {body}, {text_1}")

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

    if "df_source_ventes" in st.session_state:
        st.divider()
        st.markdown("### 📤 Télécharger le fichier actuel (même s’il reste des erreurs)")
        buf_export_anytime = dataframe_to_excel_bytes(st.session_state["df_source_ventes"])
        st.download_button(
            "📥 Télécharger maintenant",
            buf_export_anytime,
            "ventes_exportées.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    if "df_source_ventes" not in st.session_state:
        uploaded = st.file_uploader("Importe ton fichier Excel des ventes", type=["xlsx"], key="uploader_ventes")
        if uploaded:
            df = safe_read_excel(uploaded, header_row=1)
            st.session_state["df_source_ventes"] = df
            afficher_interface(df, force_recontrole=True)
    else:
        afficher_interface(st.session_state["df_source_ventes"])

run_interface()