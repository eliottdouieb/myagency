import streamlit as st
st.set_page_config(page_title="Contrôle des écritures comptables", layout="wide")


st.title("📊 Interface de Contrôle Comptable")

option = st.radio("Quel type d'écriture souhaites-tu contrôler ?", ["Achats","Ventes","Encaissements","Depenses revolut"], horizontal=True)

# if option == "Achats":
#     from controle_achats import run_interface as run_achats
#     run_achats()
    
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
    from depenses import run_interface as run_encaissements
    run_encaissements()

