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

from typing import List, Tuple
import pandas as pd


def run_ventes_checks_console(df: pd.DataFrame) -> Tuple[List[str], List[str], int, pd.DataFrame]:
    logs: List[str] = []
    factures_ko: List[str] = []

    df = df.applymap(lambda x: str(x).strip() if isinstance(x, str) else x)
    # Nettoyage Debit
    deb = (
        df["Debit"]
        .astype(str)
        .str.replace("\u00A0", " ", regex=False)      # espace insécable -> normal
        .str.replace("\u202F", " ", regex=False)      # espace fine -> normal
        .str.replace(",", ".", regex=False)           # , décimale -> .
        .str.replace(r"[^\d.\-]", "", regex=True)     # garde chiffres/point/signe -
        .str.strip()
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)  # (123,45) -> -123,45
    )
    df["Debit"] = pd.to_numeric(deb, errors="coerce").fillna(0.0)

    # Nettoyage Credit
    cred = (
        df["Credit"]
        .astype(str)
        .str.replace("\u00A0", " ", regex=False)
        .str.replace("\u202F", " ", regex=False)
        .str.replace(",", ".", regex=False)
        .str.replace(r"[^\d.\-]", "", regex=True)
        .str.strip()
        .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
    )
    df["Credit"] = pd.to_numeric(cred, errors="coerce").fillna(0.0)


    df["ordre_excel"] = range(len(df))
    grouped = df.groupby("#", sort=False)
    facture_order = df.drop_duplicates("#")[["#", "ordre_excel"]].sort_values("ordre_excel")
    ordered_groups = [grouped.get_group(facture) for facture in facture_order["#"]]

    conversion_logs_map = {}
    for group in ordered_groups:
        monnaie = group["Currency"].iloc[0]
        num_facture = group["#"].iloc[0]

        if monnaie != "€":
            symbole = monnaie.strip()
            # --- CHANGEMENT MINIMAL : détection correcte du code devise ---
            CODES = set(symbol_to_currency.values())
            code_devise = symbol_to_currency.get(symbole) or (symbole.upper() if symbole.upper() in CODES else None)
            # ----------------------------------------------------------------

            if not code_devise:
                conversion_logs_map[num_facture] = [f"❌ Facture {num_facture} : symbole devise inconnu '{symbole}'"]
                continue

            date_facture = pd.to_datetime(group["Date"].iloc[0]).strftime("%Y-%m-%d")
            try:
                taux = get_conversion_rate_frankfurter(date_facture, code_devise)
                df.loc[group.index, "Debit"] *= taux
                df.loc[group.index, "Credit"] *= taux
                df.loc[group.index, "Currency"] = "€"
                conversion_logs_map[num_facture] = [f"💱 Conversion en EUR appliquée pour la facture {num_facture} (taux : {taux})"]
            except Exception as e:
                conversion_logs_map[num_facture] = [f"❌ Erreur conversion facture {num_facture} : {str(e)}"]

    grouped = df.groupby("#", sort=False)
    facture_order = df.drop_duplicates("#")[["#", "ordre_excel"]].sort_values("ordre_excel")
    ordered_groups = [grouped.get_group(facture) for facture in facture_order["#"]]

    factures_corrigees_manuellement = []
    correction_logs_map = {}

    for group in ordered_groups:
        num_facture = group["#"].iloc[0]
        if pd.isna(num_facture):
            continue

        ligne_411 = group[group["Account General"].astype(str).str.strip() == "411000"]

        if ligne_411.shape[0] == 1:
            l411 = ligne_411.iloc[0]
            autres = group[group["Account General"].astype(str).str.strip() != "411000"]

            total_debit = group["Debit"].sum()
            total_credit = group["Credit"].sum()

            if total_debit == 0 and total_credit == 0 and len(group) >= 2:
                idx_ligne_411 = ligne_411.index[0]
                idx_autre = autres.index[0]

                df.loc[[idx_ligne_411, idx_autre], "Account General"] = l411["Account General"]
                df.loc[[idx_ligne_411, idx_autre], "Account Client"] = l411["Account Client"]

                df.loc[idx_ligne_411, "Debit"] = 1.0
                df.loc[idx_ligne_411, "Credit"] = 0.0
                df.loc[idx_autre, "Debit"] = 0.0
                df.loc[idx_autre, "Credit"] = 1.0

                if df.loc[idx_ligne_411, "Code"] != "G":
                    df.loc[idx_ligne_411, "Code"] = "G"
                if df.loc[idx_autre, "Code"] != "G":
                    df.loc[idx_autre, "Code"] = "G"

                lignes_a_supprimer = group.index.difference([idx_ligne_411, idx_autre])
                df = df.drop(index=lignes_a_supprimer)

                correction_logs_map[num_facture] = ["🔧 Erreur sur facture mise à 0 corrigée automatiquement"]
                factures_corrigees_manuellement.append(num_facture)

    grouped = df.groupby("#", sort=False)
    facture_order = df.drop_duplicates("#")[["#", "ordre_excel"]].sort_values("ordre_excel")
    ordered_groups = [grouped.get_group(facture) for facture in facture_order["#"]]

    for group in ordered_groups:
        num_facture = group["#"].iloc[0]
        erreurs = []

        if pd.isna(num_facture):
            erreurs.append("# manquant")
            continue

        if not (group["VE"] == "VE").all():
            erreurs.append("VE ≠ VE")
        if group["Date"].nunique() > 1:
            erreurs.append("Dates différentes dans une même facture")
        if group["Currency"].nunique() > 1 or group["Currency"].iloc[0] != "€":
            erreurs.append(f"Facture non en euro (valeurs : {group['Currency'].unique().tolist()})")

        first_row = group.sort_values("ordre_excel").iloc[0]
        compte_premiere_ligne = str(first_row["Account General"]).strip()
        if compte_premiere_ligne != "411000":
            erreurs.append(f"1ère ligne ≠ 411000 (valeur : {compte_premiere_ligne})")

        if not all(code in ["A", "G"] for code in group["Code"]):
            erreurs.append("Code ≠ A ou G")
        if not group["Analytic"][group["Code"] != "A"].isna().all():
            erreurs.append("Analytic ne doit être rempli que si Code = A")

        lignes_411 = group[group["Account General"].astype(str).str.strip() == "411000"]
        comptes_tiers_valides = ~lignes_411["Account Client"].astype(str).str.strip().eq("411-NO MEMBER ACCOUNT")

        # ✅ Cas spécial : 2 lignes 411000 avec Debit 1 et Credit 1, et comptes tiers valides
        if (
            len(group) == 2 and
            lignes_411.shape[0] == 2 and
            comptes_tiers_valides.all()
        ):
            l1, l2 = lignes_411.iloc[0], lignes_411.iloc[1]
            d1, c1 = l1["Debit"], l1["Credit"]
            d2, c2 = l2["Debit"], l2["Credit"]

            if (d1 == 1 and c1 == 0 and d2 == 0 and c2 == 1) or (d2 == 1 and c2 == 0 and d1 == 0 and c1 == 1):
                logs.append(f"✅ Facture {num_facture} : Cas spécial 2 lignes 411000 avec Debit/Credit inversés")
                continue

        lignes_411_bad_tiers = group[
            (group["Account General"].astype(str).str.strip() == "411000") &
            (group["Account Client"].astype(str).str.strip() == "411-NO MEMBER ACCOUNT")
        ]
        if not lignes_411_bad_tiers.empty:
            erreurs.append("Ligne 411000 avec Account Client '411-NO MEMBER ACCOUNT'")

        ligne_411 = group[group["Account General"].astype(str).str.strip() == "411000"]
        if ligne_411.shape[0] != 1 and num_facture not in factures_corrigees_manuellement:
            erreurs.append("Nombre ≠ 1 de lignes 411000")
        elif ligne_411.shape[0] == 1:
            l411 = ligne_411.iloc[0]
            autres = group[group["Account General"].astype(str).str.strip() != "411000"]

            if l411["Debit"] > 0 and l411["Credit"] == 0:
                if not (autres["Debit"] == 0).all():
                    erreurs.append("Debit ≠ 0 sur lignes ≠ 411000")
                if not (autres["Credit"] > 0).all():
                    erreurs.append("Credit ≤ 0 sur lignes ≠ 411000")

                lignes_G = group[group["Code"] != "A"]
                if round(lignes_G["Credit"].sum() - l411["Debit"], 2) != 0:
                    erreurs.append("Somme Credits ≠ Debit 411000")

            elif l411["Credit"] > 0 and l411["Debit"] == 0:
                conversion_logs_map.setdefault(num_facture, []).append(f"🔄 Facture \"{num_facture}\" détectée comme AVOIR")

                if not (autres["Credit"] == 0).all():
                    erreurs.append("Credit ≠ 0 sur lignes ≠ 411000 (cas avoir)")
                if not (autres["Debit"] > 0).all():
                    erreurs.append("Debit ≤ 0 sur lignes ≠ 411000 (cas avoir)")

                lignes_G = group[group["Code"] != "A"]
                if round(lignes_G["Debit"].sum() - l411["Credit"], 2) != 0:
                    erreurs.append("Somme Debits ≠ Credit 411000 (cas avoir)")
            else:
                erreurs.append("Ligne 411000 invalide (ni Debit > 0 ni Credit > 0)")

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

    if not factures_ko and "Name" in df.columns:
        df["Name"] = df["Name"].apply(clean_nom_client)
        logs.append("✅ Caractères spéciaux supprimée avant export.")

    if factures_ko:
        logs.append(f"\n📋 Contrôle terminé : {len(factures_ko)} facture(s) KO.")
    else:
        logs.append("\n📋 Contrôle terminé : toutes les écritures sont conformes ✅")

    return logs, factures_ko, len(factures_ko), df