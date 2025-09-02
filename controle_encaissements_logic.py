import pandas as pd
import re
from typing import List, Tuple

PAYMENT_DICT = {
    "CHÈQUE": ("CH", 1),
    "CARTE BANCAIRE": ("CB", 2),
    "TPE CARTE BANCAIRE": ("CB", 2),
    "AMEX": ("AM", 3),
    "VIREMENT BANCAIRE": ("VI", 4),
    "CASH": ("CA", 5),
}
ABBR_TO_CODE = {v[0]: v[1] for v in PAYMENT_DICT.values()}


def swap_columns_411(df: pd.DataFrame, col_c: str, col_d: str, logs: List[str]):
    mask = df[col_c].astype(str).str.startswith("411") & df[col_d].astype(str).str.startswith("411")
    df.loc[mask, [col_c, col_d]] = df.loc[mask, [col_d, col_c]].values
    logs.append(f"✅ Colonnes '{col_c}' et '{col_d}' inversées pour les lignes commençant par 411.")


def clean_name_column(df: pd.DataFrame, col_name: str, logs: List[str]):
    df[col_name] = df[col_name].astype(str).str.split("-").str[0].str.strip()
    logs.append(f"✅ Colonne '{col_name}' nettoyée (conservé avant le premier '-').")


def swap_columns(df: pd.DataFrame, col_a: str, col_b: str, logs: List[str]):
    df[col_a], df[col_b] = df[col_b].copy(), df[col_a].copy()
    logs.append(f"✅ Colonnes '{col_a}' et '{col_b}' échangées.")





def validate_invoices(df: pd.DataFrame, logs: List[str]) -> Tuple[List[str], int]:
    ko_invoices = []
    # logs.append(f"📌 Colonnes disponibles : {list(df.columns)}")


    for invoice, group in df.groupby("Invoice #"):
        debit_sum = group["Debit"].sum()
        credit_sum = group["Credit"].sum()

        if round(debit_sum - credit_sum, 2) != 0:
            logs.append(f"❌ Invoice {invoice} : Debit ≠ Credit ({debit_sum} ≠ {credit_sum})")
            ko_invoices.append(invoice)
            continue

        if len(group) != 2:
            logs.append(f"❌ Invoice {invoice} : nombre de lignes != 2.")
            ko_invoices.append(invoice)
            continue

        # 💥 Vérification spécifique sur le compte "411-NO MEMBER ACCOUNT"
        ligne_411 = group[group["Account Global"].astype(str).str.strip() == "411000"]
        if not ligne_411.empty:
            if any(ligne_411["Account Client"].astype(str).str.upper() == "411-NO MEMBER ACCOUNT"):
                logs.append(f"❌ Invoice {invoice} : Ligne 411000 avec compte Client '411-NO MEMBER ACCOUNT'")
                ko_invoices.append(invoice)
                continue
        # logs.append(f"🔍 Invoice {invoice} : comptes clients ligne 411 = {ligne_411['Account Client'].tolist()}")


        # Détection du moyen de paiement
        raw_mean = str(group["Payment Mean"].iloc[0]).strip().upper()
        abbreviation = None
        code_paiement = None
        for full, (abbr, code) in PAYMENT_DICT.items():
            if full in raw_mean:
                abbreviation = abbr
                code_paiement = code
                break

        if not abbreviation:
            logs.append(f"❌ Invoice {invoice} : moyen de paiement inconnu ({raw_mean})")
            ko_invoices.append(invoice)
            continue

        # Extraction du mois
        date_value = str(group["Date"].iloc[0])
        match = re.search(r"\d{2}/(\d{2})/\d{4}", date_value)
        if not match:
            logs.append(f"❌ Invoice {invoice} : date invalide ({date_value})")
            ko_invoices.append(invoice)
            continue
        month_code = match.group(1)

        # Génération du compte attendu
        if abbreviation in {"CH", "CB", "VI"}:
            expected_second = f"511{code_paiement}{month_code}"
        elif abbreviation == "AM":
            expected_second = f"4113{month_code}"
        elif abbreviation == "CA":
            expected_second = "530000"
        else:
            logs.append(f"❌ Invoice {invoice} : abréviation non supportée ({abbreviation})")
            ko_invoices.append(invoice)
            continue

        accounts = set(group["Account Global"].astype(str))
        if not ("411000" in accounts and expected_second in accounts):
            logs.append(f"❌ Invoice {invoice} : mauvais comptes globaux {accounts}, attendu : 411000 et {expected_second}")
            ko_invoices.append(invoice)
        else:
            logs.append(f"✅ Invoice {invoice} : OK.")

    return ko_invoices, len(ko_invoices)


def run_checks(df: pd.DataFrame) -> Tuple[List[str], List[str], int]:
    logs: List[str] = []

    swap_columns_411(df, "Account Global", "Account Client", logs)
    clean_name_column(df, "Name", logs)

    if "Payment Date" in df.columns and "Date" in df.columns:
        swap_columns(df, "Payment Date", "Date", logs)

    ko_invoices, nb_ko = validate_invoices(df, logs)

    # drop_columns(df, ["Analytics", "Payment Mean", "Payment Date", "Comment"], logs)

    if nb_ko == 0:
        logs.append("\n📋 Contrôle terminé : toutes les écritures sont conformes ✅")
    else:
        logs.append(f"\n📋 Contrôle terminé : {nb_ko} encaissement(s) non conforme(s).")

    return logs, ko_invoices, nb_ko

def rerun_check_no_member_only(df: pd.DataFrame, original_logs: List[str]) -> Tuple[List[str], List[str], int]:
    updated_logs = []
    ko_invoices = []
    invoices_checked = set()

    for log in original_logs:
        match = re.search(r"Invoice (\d+)", log)
        if match and "NO MEMBER ACCOUNT" in log:
            invoice = match.group(1)
            invoices_checked.add(invoice)

            mask_invoice_411 = (
                (df["Invoice #"].astype(str) == invoice) &
                (df["Account Global"].astype(str).str.strip() == "411000")
            )
            sub_df = df[mask_invoice_411]

            if not sub_df.empty:
                has_no_member = sub_df["Account Client"].astype(str).str.upper().eq("411-NO MEMBER ACCOUNT").any()
                if has_no_member:
                    updated_logs.append(f"❌ Invoice {invoice} : Ligne 411000 avec compte Client '411-NO MEMBER ACCOUNT'")
                    ko_invoices.append(invoice)
                else:
                    updated_logs.append(f"✅ Invoice {invoice} : compte client corrigé.")
            else:
                updated_logs.append(f"ℹ️ Invoice {invoice} : ligne 411000 introuvable.")
        else:
            updated_logs.append(log)  # log inchangé

    # Réorganisation pour maintenir l'ordre des logs
    updated_logs_clean = []
    seen = set()
    for log in updated_logs:
        match = re.search(r"Invoice (\d+)", log)
        if match:
            invoice = match.group(1)
            if invoice in seen:
                continue
            seen.add(invoice)
        updated_logs_clean.append(log)

    # Logs finaux
    updated_logs_clean.append("\n📋 Contrôle terminé :")
    if ko_invoices:
        updated_logs_clean.append(f"❌ {len(ko_invoices)} facture(s) ont encore un compte client 'NO MEMBER ACCOUNT'.")
    else:
        updated_logs_clean.append("✅ Tous les comptes clients précédemment 'NO MEMBER ACCOUNT' ont été corrigés.")

    return updated_logs_clean, ko_invoices, len(ko_invoices)
