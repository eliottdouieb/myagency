# =========================
# ====== TES IMPORTS ======
# =========================
from xlsx2csv import Xlsx2csv
from io import StringIO
import math
import pandas as pd
import numpy as np
from datetime import datetime
import requests
import streamlit as st
from io import BytesIO




# ===============================
# ====== TES OPTIONS PANDAS =====
# ===============================
# pd.set_option('display.max_rows', None)
# pd.set_option('display.max_columns', None)

# ======================================
# ====== (TON CODE D'ORIGINE) ==========
# ====== NE RIEN CHANGER CI-DESSOUS ====
# ======================================

# csv_buffer = StringIO()
# Xlsx2csv("Export achats  MYBACKOFFICE (1).xlsx", outputencoding="utf-8").convert(csv_buffer)
# csv_buffer.seek(0)

# df = pd.read_csv(csv_buffer,skiprows=1)

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
        df_current = st.session_state.df_source.copy()  # 🔄 récupère toujours l'état actuel
        with st.sidebar:
            st.markdown("### 📅 Export permanent")
            st.download_button(
                "📅 Télécharger maintenant",
                dataframe_to_excel_bytes(df_current),
                "achats_export.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="dl_sidebar_anytime"
            )




def dataframe_to_excel_bytes(df: pd.DataFrame) -> BytesIO:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        df.to_excel(writer, index=False)
    buf.seek(0)
    return buf

def _get_df_to_export_anytime(df) -> pd.DataFrame:
    return (df)
    

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



def check_lignes_comptables(df):
    num_piece=df["n° de piece"].unique()
    log_generale=[]
    Compte_Tiers_invalide=0
    achats_ko=[]
    for i in num_piece:
        log_piece=[]
        log_ko=False
        df_provisoire=df[(df["n° de piece"]==i) & (df["Code"]=="G")]
        if check_compte_tiers_invalide(df_provisoire):
            log_piece.append("Compte Tiers invalide")
            Compte_Tiers_invalide+=1
            log_ko=True
            achats_ko.append(i)

        if check_oublie_credit_ou_debit(df_provisoire):
            df.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Débit(€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'].sum()
            df.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Crédit (€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Débit(€)'].sum()
            df_provisoire.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Débit(€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'].sum()
            df_provisoire.loc[df_provisoire[df_provisoire['Compte Généraux']==401000].index, "Crédit (€)"] = df_provisoire[df_provisoire['Compte Généraux']!=401000]['Débit(€)'].sum()
            log_piece.append(f"Achat {i}- Credit ou Debit omis")

        if check_credit_egale_debit(df_provisoire)==False:
            log_piece.append(f"Credit≠Debit {df_provisoire['Débit(€)'].sum()} -- {df_provisoire['Crédit (€)'].sum()}")
            log_ko=True
        else:
            if check_lignes_vides(df_provisoire)==False:
                idx_a_supprimer = get_index_lignes_vides(df_provisoire)
                df.drop(index=idx_a_supprimer,inplace=True)
                log_piece.append(f"ligne vide supprime")

            if check_mauvais_emplacement_credit(df_provisoire)==True:
                log_piece.append(f"mauvais emplacement credit modifie")
                index_cond = df_provisoire[df_provisoire['Compte Généraux']!=401000].index[df_provisoire[df_provisoire['Compte Généraux']!=401000]['Crédit (€)'] != 0]
                credit_tmp = df.loc[index_cond, 'Crédit (€)'].copy()
                df.loc[index_cond, 'Crédit (€)'] = df.loc[index_cond, 'Débit(€)']*(-1)
                df.loc[index_cond, 'Débit(€)']  = credit_tmp * (-1)
                
            if check_mauvais_emplacement_debit(df_provisoire)==True:
                log_piece.append(f"mauvais emplacement debit modifie")
                index_cond = df_provisoire[df_provisoire['Compte Généraux']!=401000].index[df_provisoire[df_provisoire['Compte Généraux']!=401000]['Débit(€)'] != 0]
                credit_tmp = df.loc[index_cond, 'Débit(€)'].copy()
                df.loc[index_cond, 'Débit(€)'] = df.loc[index_cond, 'Crédit (€)']*(-1)
                df.loc[index_cond, 'Crédit (€)']  = credit_tmp * (-1)

            # if check_mauvais_emplacement(df_provisoire)==True:
            #     mask = a[a['Débit(€)']!=0].any()
            #     # Inversion des valeurs
            #     df.loc[mask, ["Débit(€)", "Crédit (€)"]] = df.loc[mask, ["Crédit (€)", "Débit(€)"]].values

        if log_ko==False:
             log_generale.append(f"✅ Achat {i} : OK , {log_piece}")
        if log_ko==True:
            log_generale.append(f"❌ Achat {i} : KO , {log_piece}")

    return log_generale,Compte_Tiers_invalide,achats_ko

# ======================================
# ====== (FIN TON CODE ORIGINE) ========
# ======================================


# ======================================
# ========== COUCHE STREAMLIT ==========
# ========== (AJOUT SANS MODIFIER TON CODE)
# ======================================


# st.set_page_config(page_title="Contrôle Achats — MyBackOffice", page_icon="📊", layout="wide")
# st.title("📊 Contrôle automatique des écritures d'achats")

# st.markdown("Téléverse ton export Excel puis lance les contrôles. Ton code d’analyse est utilisé **tel quel**.")

# # Uploader
# uploaded = st.file_uploader("Importe ton fichier Excel des achats (.xlsx)", type=["xlsx"])

# # Pour conserver l'état du df courant (celui que tes fonctions modifient)
# if "df_state" not in st.session_state:
#     st.session_state.df_state = None

# def _xlsx_to_df(file) -> pd.DataFrame:
#     """Reproduit exactement ta logique xlsx2csv -> read_csv(skiprows=1)."""
#     buf = StringIO()
#     Xlsx2csv(file, outputencoding="utf-8").convert(buf)
#     buf.seek(0)
#     return pd.read_csv(buf, skiprows=1)

# col1, col2 = st.columns([1,1])

# with col1:
#     if uploaded is not None and st.button("🚀 Lancer les contrôles"):
#         # Charger dans df_state selon TA logique (sans modifier ton code)
#         st.session_state.df_state = _xlsx_to_df(uploaded)
#         # Exécuter les étapes que tu fais déjà (exactement les mêmes appels)
#         remplie_numero_piece_manquant(st.session_state.df_state)
#         suppression_445660_dans_Compte_tiers(st.session_state.df_state)
#         logs, nb_invalides = check_lignes_comptables(st.session_state.df_state)

#         st.success(f"Contrôles terminés — Compte Tiers invalide: {nb_invalides}")
#         st.subheader("📝 Logs")
#         st.code("\n".join(logs), language="text")

# with col2:
#     st.subheader("👀 Aperçu du DataFrame (après contrôles)")
#     if st.session_state.df_state is not None:
#         st.dataframe(st.session_state.df_state, use_container_width=True, height=500)
#     else:
#         st.info("Aucun fichier chargé pour l’instant.")

# st.divider()
# st.subheader("📥 Export")

# def _df_to_excel_bytes(df_export: pd.DataFrame) -> BytesIO:
#     buf = BytesIO()
#     # tu utilises xlsxwriter by default ; si env ancien ça peut warning,
#     # mais on n'altère pas ton code métier, on fait juste l'export.
#     with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
#         df_export.to_excel(writer, index=False)
#     buf.seek(0)
#     return buf

# colA, colB = st.columns(2)
# with colA:
#     if st.session_state.df_state is not None:
#         st.download_button(
#             "⬇️ Télécharger Excel (xlsx)",
#             data=_df_to_excel_bytes(st.session_state.df_state),
#             file_name="achats_corriges.xlsx",
#             mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
#             use_container_width=True
#         )
# with colB:
#     if st.session_state.df_state is not None:
#         st.download_button(
#             "⬇️ Télécharger CSV",
#             data=st.session_state.df_state.to_csv(index=False).encode("utf-8"),
#             file_name="achats_corriges.csv",
#             mime="text/csv",
#             use_container_width=True
#         )

# st.caption("Astuce : si vous voyez un avertissement `xlsxwriter` trop ancien, mettez à jour `xlsxwriter` (>= 1.4.3) ou utilisez l’export CSV.")




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
                df_unique[["n° de piece", "Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge"]],
                key=editor_key,
                hide_index=True,
            )

            if st.button("✅ Valider les corrections", key=validate_key):
                for _, r in edited.iterrows():
                    idx = df[
                        (df["Libelle"] == r["Libelle"]) 
                        & (df["Compte Généraux"] == 401000)
                        & (df["n° de piece"] == r["n° de piece"])
                    ].index
                    if not idx.empty:
                        df.loc[idx, ["Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge"]] = \
                            r[["Compte Tiers", "Débit(€)", "Crédit (€)", "Libelle", "Concierge"]].values

                st.session_state.df_source = df
                st.success("✅ Modifications enregistrées. Clique sur le bouton ci-dessous pour relancer le contrôle.")

                if st.button("🔁 Relancer le contrôle", key=rerun_key):
                    # relancer uniquement la fonction (pas de sys.exit)
                    log_generale, Compte_Tiers_invalide, achats_ko = check_lignes_comptables(df)
                    st.session_state.ko_cycle += 1  # ➜ nouvelles clés uniques au prochain rendu
                    st.success("Le contrôle a été relancé ✅")
                    st.experimental_rerun()  # force un rerender propre

        #         # ── AJOUT: push API vers CRM par n° de pièce ─────────────────────
        #         api_logs = []
        #         with st.spinner("Mise à jour des comptes tiers dans le CRM..."):
        #             for _, r in edited.iterrows():
        #                 piece = str(r["n° de piece"]).strip()
        #                 mask_piece = (df["n° de piece"].astype(str).str.strip() == piece) & (df["Compte Généraux"] == "401000")
        #                 if not mask_piece.any():
        #                     api_logs.append(f"⚠️ Pièce {piece}: aucune ligne 401000 trouvée, ignorée.")
        #                     continue

        #                 date_facture_piece = df.loc[mask_piece, "Date Facture"].iloc[0] if "Date Facture" in df.columns else ""
        #                 date_iso = _to_date_iso(date_facture_piece)
        #                 inv_num = build_invoice_number_from_piece_and_date(piece, date_facture_piece)
        #                 compte_value = str(r.get("Compte Tiers", "")).strip()

        #                 if not inv_num or not date_iso or not compte_value:
        #                     api_logs.append(
        #                         f"⚠️ Pièce {piece}: infos incomplètes — InvoiceNumber='{inv_num}', date='{date_iso}', Compte='{compte_value}'"
        #                     )
        #                     continue

        #                 status, body = push_compte_tiers_to_crm(inv_num, compte_value, date_iso)
        #                 if status and 200 <= status < 300:
        #                     api_logs.append(f"✅ CRM ok — Pièce {piece} → {compte_value} | Invoice {inv_num} | date {date_iso} (HTTP {status})")
        #                 else:
        #                     api_logs.append(f"❌ CRM ko — Pièce {piece} → {compte_value} | Invoice {inv_num} | date {date_iso} (HTTP {status}) | {body}")

        #         with st.expander("Détails des mises à jour CRM"):
        #             for line in api_logs:
        #                 st.write(line)
        #         # ────────────────────────────────────────────────────────────────

                # if st.button("🔁 Relancer le contrôle"):
                #     log_generale,Compte_Tiers_invalide,achats_ko=check_lignes_comptables(df)
                #     # st.session_state.logs = logs  # tu sauvegardes si tu veux les réafficher
                #     st.success("Le contrôle a été relancé ✅")

        # else:
        #     st.success("🎉 Plus aucun achat KO. Tu peux exporter le fichier corrigé.")
        #     buf = dataframe_to_excel_bytes(df)
        #     st.download_button(
        #         "📥 Télécharger le fichier corrigé",
        #         buf,
        #         "achats_corriges.xlsx",
        #         mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        #     )
