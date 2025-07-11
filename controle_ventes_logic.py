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

def get_conversion_rate_frankfurter(date: str, from_currency: str, to_currency: str = "EUR") -> float:
    url = f"https://api.frankfurter.app/{date}"
    params = {"from": from_currency, "to": to_currency}
    response = requests.get(url, params=params)
    data = response.json()

    if "rates" not in data or to_currency not in data["rates"]:
        print("❌ Erreur dans la réponse :", data)
        return 1.0  # Valeur de secours

    return data["rates"][to_currency]

def run_ventes_checks_console(df: pd.DataFrame) -> Tuple[List[str], List[str], int, pd.DataFrame]:
    logs = []
    factures_ko = []

    df.columns = [
        "Code journal", "Date de facture", "Compte général", "Compte tiers",
        "Concierge", "Nom client + service", "Numéro de facture",
        "Débit", "Crédit", "Monnaie", "Analytique", "Code"
    ]

    df = df.applymap(lambda x: str(x).strip() if isinstance(x, str) else x)
    df["Nom client + service"] = df["Nom client + service"].apply(clean_nom_client)
    df["Débit"] = df["Débit"].astype(str).str.replace(",", ".").str.replace(r"[^\d.]", "", regex=True).astype(float)
    df["Crédit"] = df["Crédit"].astype(str).str.replace(",", ".").str.replace(r"[^\d.]", "", regex=True).astype(float)

    df["ordre_excel"] = range(len(df))
    grouped = df.groupby("Numéro de facture", sort=False)
    facture_order = df.drop_duplicates("Numéro de facture")[["Numéro de facture", "ordre_excel"]].sort_values("ordre_excel")
    ordered_groups = [grouped.get_group(facture) for facture in facture_order["Numéro de facture"]]

    # 🔁 Conversion des devises ≠ EUR
    for group in ordered_groups:
        monnaie = group["Monnaie"].iloc[0]
        if monnaie != "€":
            symbole = monnaie.strip()
            code_devise = next((code for code, info in symbol_to_currency.items() if info == symbole or info == code), None)

            if not code_devise:
                logs.append(f"❌ Facture {group['Numéro de facture'].iloc[0]} : symbole devise inconnu '{symbole}'")
                continue

            date_facture = pd.to_datetime(group["Date de facture"].iloc[0]).strftime("%Y-%m-%d")
            try:
                taux = get_conversion_rate_frankfurter(date_facture, code_devise)
                df.loc[group.index, "Débit"] *= taux
                df.loc[group.index, "Crédit"] *= taux
                df.loc[group.index, "Monnaie"] = "€"
                logs.append(f"💱 Conversion en EUR appliquée pour la facture {group['Numéro de facture'].iloc[0]} (taux : {taux})")
            except Exception as e:
                logs.append(f"❌ Erreur conversion facture {group['Numéro de facture'].iloc[0]} : {str(e)}")

    # Recalcul des groupes après conversion
    grouped = df.groupby("Numéro de facture", sort=False)
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

        # 🔴 Nouveau test ici
        lignes_411_bad_tiers = group[
            (group["Compte général"].astype(str).str.strip() == "411000") &
            (group["Compte tiers"].astype(str).str.strip() == "411-NO MEMBER ACCOUNT")
        ]
        if not lignes_411_bad_tiers.empty:
            erreurs.append("Ligne 411000 avec compte tiers '411-NO MEMBER ACCOUNT'")

        ligne_411 = group[group["Compte général"].astype(str).str.strip() == "411000"]
        if ligne_411.shape[0] != 1:
            erreurs.append("Nombre ≠ 1 de lignes 411000")
        else:
            l411 = ligne_411.iloc[0]
            if l411["Débit"] <= 0:
                erreurs.append("Débit ligne 411000 ≤ 0")
            if l411["Crédit"] != 0:
                erreurs.append("Crédit ligne 411000 ≠ 0")

            autres = group[group["Compte général"].astype(str).str.strip() != "411000"]
            if not (autres["Débit"] == 0).all():
                erreurs.append("Débit ≠ 0 sur lignes ≠ 411000")
            if not (autres["Crédit"] > 0).all():
                erreurs.append("Crédit ≤ 0 sur lignes ≠ 411000")

            lignes_G = group[group["Code"] != "A"]
            if round(lignes_G["Crédit"].sum() - l411["Débit"], 2) != 0:
                erreurs.append("Somme crédits ≠ Débit 411000")

        statut = "❌" if erreurs else "✅"
        logs.append(f"{statut} Facture {num_facture} : {'KO' if erreurs else 'OK'}")
        for e in erreurs:
            logs.append(f"   🔻 {e}")

        if erreurs:
            factures_ko.append(num_facture)

    df.drop(columns=["ordre_excel"], inplace=True)
    if not factures_ko and "Concierge" in df.columns:
        df.drop(columns=["Concierge"], inplace=True)
        logs.append("✅ Colonne Concierge supprimée avant export.")

    if factures_ko:
        logs.append(f"\n📋 Contrôle terminé : {len(factures_ko)} facture(s) KO.")
    else:
        logs.append("\n📋 Contrôle terminé : toutes les écritures sont conformes ✅")

    return logs, factures_ko, len(factures_ko), df
