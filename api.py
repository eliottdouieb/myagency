# =========================
# ====== TES IMPORTS ======
# =========================
from xlsx2csv import Xlsx2csv
from io import StringIO, BytesIO
import math
import pandas as pd
import numpy as np
from datetime import datetime, date
import requests
import streamlit as st

def call_crm_once():
    BASE_URL = st.secrets["crm"]["base_url"]
    AUTH_URL = f"{BASE_URL}/api/appMember/concierge/login"
    ACCOUNTING_URL_TMPL = f"{BASE_URL}/api/myagency/controller/accounting/{{ConciergeHash}}"

    EMAIL = st.secrets["crm"]["email"]
    PASSWORD = st.secrets["crm"]["password"]
    if not PASSWORD:
        raise RuntimeError("Missing CRM_PASSWORD.")

    # --- Auth
    auth_resp = requests.post(AUTH_URL, json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    auth_resp.raise_for_status()
    auth_ct = (auth_resp.headers.get("content-type") or "").lower()
    auth_data = auth_resp.json() if "application/json" in auth_ct else {}
    if not auth_data.get("success"):
        raise RuntimeError(f"Login failed: {auth_data}")

    ConciergeHash = str(auth_data.get("ConciergeHash", "")).strip()
    ApiToken      = str(auth_data.get("ApiToken", "")).strip()
    if not ConciergeHash or not ApiToken:
        raise RuntimeError("Missing ConciergeHash or ApiToken in login response.")

    # --- Update
    url = ACCOUNTING_URL_TMPL.format(ConciergeHash=ConciergeHash)
    payload = {
        "payload": {
            "InvoiceNumber": "54147",
            "type": "member",
            "field": "vente",
            "value": "411LO",
            "date": "2025-06-26",
        }
    }
    headers = {
        "Content-Type": "application/json",
        "ApiToken": ApiToken,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        ctype = (resp.headers.get("content-type") or "").lower()
        try:
            body = resp.json() if "application/json" in ctype else {"raw": resp.text}
        except ValueError:
            body = {"raw": resp.text}

        # Affichage propre
        st.json({
            "status": resp.status_code,
            "success": body.get("success", None),
            "message": body.get("message", ""),
            "body": body,
            "sent_payload": payload,
        })

    except requests.RequestException as e:
        st.error(f"Erreur HTTP: {e}")

# Exemple d’appel (via un bouton pour éviter l’exécution à l’import)
if st.button("Mettre à jour le CRM"):
    call_crm_once()
