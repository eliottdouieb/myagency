
import pandas as pd
import re
from typing import List, Tuple

REGEX_NUM_PIECE = re.compile(r"^(0[1-9]|1[0-2])-\d+$")
REGEX_DATE_2025 = re.compile(r"^\d{2}/\d{2}/2025$")

COLONNES_TEXTE = [
    "Code journal", "Date Facture", "Compte Généraux", "Compte Tiers",
    "Libelle", "Concierge", "n° de piece", "Analytique", "Code"
]
COLONNES_MONTANTS = ["Débit(€)", "Crédit (€)"]

def run_checks(df: pd.DataFrame) -> Tuple[List[str], List[str], int]:
    logs: List[str] = []
    achats_ko: List[str] = []
    indices_a_suppr: List[int] = []

    for col in COLONNES_TEXTE:
        df[col] = (
            df[col]
            .astype(str)
            .str.strip()
            .str.upper()
            .replace({"NAN": "", "NONE": ""})
        )

    def inc(code: str) -> str:
        mois, num = code.split("-")
        return f"{mois}-{int(num)+1}"

    last_code = None
    for i, row in df.iterrows():
        cur = df.at[i, "n° de piece"]
        if row["Compte Généraux"] == "401000":
            if pd.isna(cur) or cur.strip() == "":
                new_code = "01-01" if last_code is None else inc(last_code)
                df.at[i, "n° de piece"] = new_code
                last_code = new_code
            else:
                last_code = cur.strip()
        else:
            if (pd.isna(cur) or cur.strip() == "") and last_code:
                df.at[i, "n° de piece"] = last_code
    logs.append("✅ Les n° de pièce manquants ont été remplis automatiquement.")

    mask_445 = (df["Compte Généraux"] == "445660") & (df["Compte Tiers"] == "445660")
    df.loc[mask_445, "Compte Tiers"] = ""
    logs.append("✅ La colonne Compte Tiers ne comprend plus de 445660 mal placés.")

    for col in COLONNES_MONTANTS:
        s = (
            df[col]
            .astype(str)
            .str.replace(",", ".", regex=False)   # virgule -> point
            .str.replace(" ", "", regex=False)    # espaces
        )
        # extrait un nombre (avec signe) si présent, sinon 0
        s = s.str.extract(r"(-?\d+(?:\.\d+)?)", expand=False).fillna("0")
        df[col] = pd.to_numeric(s, errors="coerce").fillna(0.0)


    def check_achat(group: pd.DataFrame, idx: str) -> Tuple[List[str], List[str], bool]:
        errors, corrections = [], []

        if not (group["Code journal"] == "AC").all():
            errors.append("Code journal différent de AC")
        if not group["Date Facture"].astype(str).apply(lambda d: bool(REGEX_DATE_2025.match(d))).all():
            errors.append("Date Facture hors format JJ/MM/2025")
        if not REGEX_NUM_PIECE.match(idx):
            errors.append("Format n° de pièce invalide")
        if group["Libelle"].nunique() > 1 or group["Concierge"].nunique() > 1:
            errors.append("Libelle ou Concierge non identiques")

        ligne_401 = group[group["Compte Généraux"] == "401000"]
        if ligne_401.empty:
            errors.append("Manque ligne 401000")
            return errors, corrections, False
        if len(ligne_401) > 1:
            errors.append("Plusieurs lignes 401000")
            return errors, corrections, False

        l401 = ligne_401.iloc[0]
        idx_401 = l401.name
        d401, c401 = l401["Débit(€)"], l401["Crédit (€)"]

        autres = group[(group["Compte Généraux"] != "401000") & (group["Code"] != "A")]
        s_deb, s_cred = autres["Débit(€)"].sum(), autres["Crédit (€)"].sum()
        
        if d401 == 0 and c401 == 0:
            if s_cred > 0 and s_deb == 0:
                df.at[idx_401, "Débit(€)"] = s_cred
                d401 = s_cred
                corrections.append(f"Correction automatique : Débit 401000 mis à {s_cred:.2f}")
            elif s_deb > 0 and s_cred == 0:
                df.at[idx_401, "Crédit (€)"] = s_deb
                c401 = s_deb
                corrections.append(f"Correction automatique : Crédit 401000 mis à {s_deb:.2f}")
            else:
                errors.append("Ligne 401000 vide et incohérente")

        facture_ok = d401 == 0 and c401 > 0
        avoir_ok = c401 == 0 and d401 > 0
        is_avoir = avoir_ok

        if not (facture_ok or avoir_ok):
            errors.append("🆘 URGENCE: Somme des débits ≠ somme des crédits ")

        if not str(l401["Compte Tiers"]).startswith("401"):
            errors.append("Ligne 401000 : Compte Tiers invalide (doit commencer par 401)")

        comptes_autorises = {"604110", "604000", "604900", "445660"}
        if (group[group["Compte Généraux"] != "401000"]["Compte Tiers"]
            .fillna("").str.strip().ne("")).any():
            errors.append("Autres lignes : Compte Tiers doit être vide")
        if not group[group["Compte Généraux"] != "401000"]["Compte Généraux"].isin(comptes_autorises).all():
            errors.append("Comptes Généraux invalides")

        if facture_ok:
            if not (autres["Débit(€)"] > 0).all():
                errors.append("Facture : Débit <= 0 sur lignes de charge")
            if (autres["Crédit (€)"] != 0).any():
                errors.append("Facture : Crédit non nul")
        elif avoir_ok:
            if not (autres["Crédit (€)"] > 0).all():
                errors.append("Avoir : Crédit <= 0")
            if (autres["Débit(€)"] != 0).any():
                errors.append("Avoir : Débit non nul")

                # --- NOUVEAU : équilibre interne des lignes G ---
        lignes_G_pures = group[group["Code"] == "G"]
        g_deb = round(lignes_G_pures["Débit(€)"].sum(), 2)
        g_cred = round(lignes_G_pures["Crédit (€)"].sum(), 2)
        g_ecart = round(g_deb - g_cred, 2)

        if g_ecart != 0:
            errors.append(
                f"🆘 URGENCE: Lignes 'G' déséquilibrées pour {idx} "
                f"(Débit total={g_deb:.2f}, Crédit total={g_cred:.2f}, écart={g_ecart:+.2f})"
            )


        lignes_G = group[group["Code"] != "A"]
        if facture_ok and round(lignes_G["Débit(€)"].sum() - c401, 2) != 0:
            errors.append("Somme Débit ≠ Crédit 401000")
        if avoir_ok and round(lignes_G["Crédit (€)"].sum() - d401, 2) != 0:
            errors.append("Somme Crédit ≠ Débit 401000")

        return errors, corrections, is_avoir

    erreurs_globales = 0
    for npiece, achat in df.groupby("n° de piece", sort=False):
        err, corr, is_avoir = check_achat(achat, npiece)

        has_tiers_err = any("COMPTE TIERS INVALIDE" in e.upper() for e in err)
        statut_ko = has_tiers_err
        label = "❌" if statut_ko else "✅"

        logs.append(f"{label} Achat {npiece} : {'KO' if statut_ko else 'OK'}")

        for c in corr:
            logs.append(f"   🛠️  {c}")

        if is_avoir:
            logs.append(f"   🔄 Achat {npiece} détecté comme AVOIR")

        for e in err:
            if "URGENCE" in e:
                bullet = "🆘"
            elif "COMPTE TIERS INVALIDE" in e.upper():
                bullet = "🔻"
            else:
                bullet = "🟢"
            logs.append(f"   {bullet} {e}")

        achat_corrige = df.loc[achat.index]
        mask_vides = (
            (achat_corrige["Débit(€)"] == 0) &
            (achat_corrige["Crédit (€)"] == 0) &
            (achat_corrige["Compte Généraux"] != "401000")
        )
        lignes_vides = achat_corrige[mask_vides]
        if not lignes_vides.empty:
            for idx, row in lignes_vides.iterrows():
                logs.append(
                    f"   🗑️  Suppression ligne vide (index {idx}, Compte {row['Compte Généraux']})"
                )
            indices_a_suppr.extend(lignes_vides.index)

        if statut_ko:
            erreurs_globales += 1
            achats_ko.append(npiece)

    if indices_a_suppr:
        df.drop(index=indices_a_suppr, inplace=True)
        logs.append(f"\n✅ {len(indices_a_suppr)} ligne(s) vide(s) supprimée(s).")

    if erreurs_globales:
        logs.append(f"\n📋 Contrôle terminé : {erreurs_globales} achat(s) non conforme(s).")
    else:
        logs.append("\n📋 Contrôle terminé : toutes les écritures sont conformes ✅")

    return logs, achats_ko, erreurs_globales

