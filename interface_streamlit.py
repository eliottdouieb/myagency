import streamlit as st
st.set_page_config(page_title="Contrôle des écritures comptables", layout="wide")

st.title("📊 Interface de Contrôle Comptable")

option = st.radio(
    "Quel type d'écriture souhaites-tu contrôler ?",
    ["Achats", "Ventes", "Encaissements", "Depenses revolut", "Depenses revolut v2"],
    horizontal=True,
)

if option == "Achats":
    from achats_v2 import run_interface as run_achats
    run_achats()

elif option == "Ventes":
    from ventes_v3 import run_interface as run_ventes
    run_ventes()

elif option == "Encaissements":
    from encaissements_v2 import run_interface as run_encaissements
    run_encaissements()

elif option == "Depenses revolut":
    from depenses import run_interface as run_dep
    run_dep()

elif option == "Depenses revolut v2":                 # ← doit matcher l'option du radio
    from depenses_match_id import run_interface as run_dep_v2
    run_dep_v2()