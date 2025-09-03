import pandas as pd
import re
import requests
from typing import List, Tuple

def clean_nom_client(txt: str) -> str:
    return re.sub(r"[^\w\s]", "", str(txt))

symbol_to_currency = {
    "A$": "AUD", "лв": "BGN", "R$": "BRL", "C$": "CAD", "CHF": "CHF", "¥": "JPY",
    "Kč": "CZK", "kr": "SEK", "€": "EUR", "£": "GBP", "HK$": "HKD", "Ft": "HUF",
    "Rp": "IDR", "₪": "ILS", "₹": "INR", "NZ$": "NZD", "$": "USD", "₩": "KRW",
    "₱": "PHP", "zł": "PLN", "lei": "RON", "S$": "SGD", "฿": "THB", "₺": "TRY", "R": "ZAR"
}

from datetime import datetime

def _to_iso_date(date_str: str) -> str:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    raise ValueError(f"Format de date non supporté: {date_str}")

def get_conversion_rate_frankfurter(date: str, from_currency: str, to_currency: str = "EUR") -> float:
    date_iso = _to_iso_date(date)
    url = f"https://api.frankfurter.app/{date_iso}"
    params = {"from": from_currency.upper(), "to": to_currency.upper()}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    rate = data.get("rates", {}).get(to_currency.upper())
    if rate is None:
        raise ValueError(f"Taux introuvable dans la réponse: {data}")
    return float(rate)

def run_ventes_checks_console(df: pd.DataFrame) -> Tuple[List[str], List[str], int, pd.DataFrame]:
    logs = []
    factures_ko = []

    df.columns = [
        "Code journal", "Date de facture", "Compte général", "Compte tiers",
        "Concierge", "Nom client + service", "Numéro de facture",
        "Débit", "Crédit", "Monnaie", "Analytique", "Code"
    ]

    df = df.applymap(lambda x: str(x).strip() if isinstance(x, str) else x)
    # Nettoyage Débit
    deb = (
        df["Débit"]
        .astype(str)
        .str.replace("\u00A0", " ", regex=False)      # espace insécable -> normal
        .str.replace("\u202F", " ", regex=False)      # espace fine -> normal
        .str.replace(",", ".", regex=False)           # , décimale -> .
        .str.replace(r"[^\d.\-]", "", regex=True)     # garde chiffres/point/signe -
        .str.strip()
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # (123,45) -> -123,45
    )
    df["Débit"] = pd.to_numeric(deb, errors="coerce").fillna(0.0)

    # Nettoyage Crédit
    cred = (
        df["Crédit"]
        .astype(str)
        .str.replace("\u00A0", " ", regex=False)
        .str.replace("\u202F", " ", regex=False)
        .str.replace(",", ".", regex=False)
        .str.replace(r"[^\d.\-]", "", regex=True)
        .str.strip()
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    )
    df["Crédit"] = pd.to_numeric(cred, errors="coerce").fillna(0.0)


    df["ordre_excel"] = range(len(df))
    grouped = df.groupby("Numéro de facture", sort=False)
    facture_order = df.drop_duplicates("Numéro de facture")[["Numéro de facture", "ordre_excel"]].sort_values("ordre_excel")
    ordered_groups = [grouped.get_group(facture) for facture in facture_order["Numéro de facture"]]

    conversion_logs_map = {}
    for group in ordered_groups:
        monnaie = group["Monnaie"].iloc[0]
        num_facture = group["Numéro de facture"].iloc[0]

        if monnaie != "€":
            symbole = monnaie.strip()
            # --- CHANGEMENT MINIMAL : détection correcte du code devise ---
            CODES = set(symbol_to_currency.values())
            code_devise = symbol_to_currency.get(symbole) or (symbole.upper() if symbole.upper() in CODES else None)
            # ----------------------------------------------------------------

            if not code_devise:
                conversion_logs_map[num_facture] = [f"❌ Facture {num_facture} : symbole devise inconnu '{symbole}'"]
                continue

            date_facture = pd.to_datetime(group["Date de facture"].iloc[0]).strftime("%Y-%m-%d")
            try:
                taux = get_conversion_rate_frankfurter(date_facture, code_devise)
                df.loc[group.index, "Débit"] *= taux
                df.loc[group.index, "Crédit"] *= taux
                df.loc[group.index, "Monnaie"] = "€"
                conversion_logs_map[num_facture] = [f"💱 Conversion en EUR appliquée pour la facture {num_facture} (taux : {taux})"]
            except Exception as e:
                conversion_logs_map[num_facture] = [f"❌ Erreur conversion facture {num_facture} : {str(e)}"]

    grouped = df.groupby("Numéro de facture", sort=False)
    facture_order = df.drop_duplicates("Numéro de facture")[["Numéro de facture", "ordre_excel"]].sort_values("ordre_excel")
    ordered_groups = [grouped.get_group(facture) for facture in facture_order["Numéro de facture"]]

    factures_corrigees_manuellement = []
    correction_logs_map = {}

    for group in ordered_groups:
        num_facture = group["Numéro de facture"].iloc[0]
        if pd.isna(num_facture):
            continue

        ligne_411 = group[group["Compte général"].astype(str).str.strip() == "411000"]

        if ligne_411.shape[0] == 1:
            l411 = ligne_411.iloc[0]
            autres = group[group["Compte général"].astype(str).str.strip() != "411000"]

            total_debit = group["Débit"].sum()
            total_credit = group["Crédit"].sum()

            if total_debit == 0 and total_credit == 0 and len(group) >= 2:
                idx_ligne_411 = ligne_411.index[0]
                idx_autre = autres.index[0]

                df.loc[[idx_ligne_411, idx_autre], "Compte général"] = l411["Compte général"]
                df.loc[[idx_ligne_411, idx_autre], "Compte tiers"] = l411["Compte tiers"]

                df.loc[idx_ligne_411, "Débit"] = 1.0
                df.loc[idx_ligne_411, "Crédit"] = 0.0
                df.loc[idx_autre, "Débit"] = 0.0
                df.loc[idx_autre, "Crédit"] = 1.0

                if df.loc[idx_ligne_411, "Code"] != "G":
                    df.loc[idx_ligne_411, "Code"] = "G"
                if df.loc[idx_autre, "Code"] != "G":
                    df.loc[idx_autre, "Code"] = "G"

                lignes_a_supprimer = group.index.difference([idx_ligne_411, idx_autre])
                df = df.drop(index=lignes_a_supprimer)

                correction_logs_map[num_facture] = ["🔧 Erreur sur facture mise à 0 corrigée automatiquement"]
                factures_corrigees_manuellement.append(num_facture)

    grouped = df.groupby("Numéro de facture", sort=False)
    facture_order = df.drop_duplicates("Numéro de facture")[["Numéro de facture", "ordre_excel"]].sort_values("ordre_excel")
    ordered_groups = [grouped.get_group(facture) for facture in facture_order["Numéro de facture"]]

    for group in ordered_groups:
        num_facture = group["Numéro de facture"].iloc[0]
        erreurs = []

        if pd.isna(num_facture):
            erreurs.append("Numéro de facture manquant")
            continue

        if not (group["Code journal"] == "VE").all():
            erreurs.append("Code journal ≠ VE")
        if group["Date de facture"].nunique() > 1:
            erreurs.append("Dates différentes dans une même facture")
        if group["Monnaie"].nunique() > 1 or group["Monnaie"].iloc[0] != "€":
            erreurs.append(f"Facture non en euro (valeurs : {group['Monnaie'].unique().tolist()})")

        first_row = group.sort_values("ordre_excel").iloc[0]
        compte_premiere_ligne = str(first_row["Compte général"]).strip()
        if compte_premiere_ligne != "411000":
            erreurs.append(f"1ère ligne ≠ 411000 (valeur : {compte_premiere_ligne})")

        if not all(code in ["A", "G"] for code in group["Code"]):
            erreurs.append("Code ≠ A ou G")
        if not group["Analytique"][group["Code"] != "A"].isna().all():
            erreurs.append("Analytique ne doit être rempli que si Code = A")

        lignes_411 = group[group["Compte général"].astype(str).str.strip() == "411000"]
        comptes_tiers_valides = ~lignes_411["Compte tiers"].astype(str).str.strip().eq("411-NO MEMBER ACCOUNT")

        # ✅ Cas spécial : 2 lignes 411000 avec Débit 1 et Crédit 1, et comptes tiers valides
        if (
            len(group) == 2 and
            lignes_411.shape[0] == 2 and
            comptes_tiers_valides.all()
        ):
            l1, l2 = lignes_411.iloc[0], lignes_411.iloc[1]
            d1, c1 = l1["Débit"], l1["Crédit"]
            d2, c2 = l2["Débit"], l2["Crédit"]

            if (d1 == 1 and c1 == 0 and d2 == 0 and c2 == 1) or (d2 == 1 and c2 == 0 and d1 == 0 and c1 == 1):
                logs.append(f"✅ Facture {num_facture} : Cas spécial 2 lignes 411000 avec Débit/Crédit inversés")
                continue

        lignes_411_bad_tiers = group[
            (group["Compte général"].astype(str).str.strip() == "411000") &
            (group["Compte tiers"].astype(str).str.strip() == "411-NO MEMBER ACCOUNT")
        ]
        if not lignes_411_bad_tiers.empty:
            erreurs.append("Ligne 411000 avec compte tiers '411-NO MEMBER ACCOUNT'")

        ligne_411 = group[group["Compte général"].astype(str).str.strip() == "411000"]
        if ligne_411.shape[0] != 1 and num_facture not in factures_corrigees_manuellement:
            erreurs.append("Nombre ≠ 1 de lignes 411000")
        elif ligne_411.shape[0] == 1:
            l411 = ligne_411.iloc[0]
            autres = group[group["Compte général"].astype(str).str.strip() != "411000"]

            if l411["Débit"] > 0 and l411["Crédit"] == 0:
                if not (autres["Débit"] == 0).all():
                    erreurs.append("Débit ≠ 0 sur lignes ≠ 411000")
                if not (autres["Crédit"] > 0).all():
                    erreurs.append("Crédit ≤ 0 sur lignes ≠ 411000")

                lignes_G = group[group["Code"] != "A"]
                if round(lignes_G["Crédit"].sum() - l411["Débit"], 2) != 0:
                    erreurs.append("Somme crédits ≠ Débit 411000")

            elif l411["Crédit"] > 0 and l411["Débit"] == 0:
                conversion_logs_map.setdefault(num_facture, []).append(f"🔄 Facture \"{num_facture}\" détectée comme AVOIR")

                if not (autres["Crédit"] == 0).all():
                    erreurs.append("Crédit ≠ 0 sur lignes ≠ 411000 (cas avoir)")
                if not (autres["Débit"] > 0).all():
                    erreurs.append("Débit ≤ 0 sur lignes ≠ 411000 (cas avoir)")

                lignes_G = group[group["Code"] != "A"]
                if round(lignes_G["Débit"].sum() - l411["Crédit"], 2) != 0:
                    erreurs.append("Somme débits ≠ Crédit 411000 (cas avoir)")
            else:
                erreurs.append("Ligne 411000 invalide (ni débit > 0 ni crédit > 0)")

        statut = "❌" if erreurs else "✅"
        logs.append(f"{statut} Facture {num_facture} : {'KO' if erreurs else 'OK'}")

        if num_facture in correction_logs_map:
            logs.extend([f"   {l}" for l in correction_logs_map[num_facture]])
        if num_facture in conversion_logs_map:
            logs.extend([f"   {l}" for l in conversion_logs_map[num_facture]])

        for e in erreurs:
            logs.append(f"   🔻 {e}")

        if erreurs:
            factures_ko.append(num_facture)

    factures_ko = [f for f in factures_ko if f not in factures_corrigees_manuellement]

    df.drop(columns=["ordre_excel"], inplace=True)
    if not factures_ko and "Concierge" in df.columns:
        df.drop(columns=["Concierge"], inplace=True)
        logs.append("✅ Colonne Concierge supprimée avant export.")

    if not factures_ko and "Nom client + service" in df.columns:
        df["Nom client + service"] = df["Nom client + service"].apply(clean_nom_client)
        logs.append("✅ Caractères spéciaux supprimée avant export.")

    if factures_ko:
        logs.append(f"\n📋 Contrôle terminé : {len(factures_ko)} facture(s) KO.")
    else:
        logs.append("\n📋 Contrôle terminé : toutes les écritures sont conformes ✅")

    return logs, factures_ko, len(factures_ko), df
