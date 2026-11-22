#!/usr/bin/env python3

# Vivekaḥ - Portfolio Insights
# Author: Balaji Sundaresan
# List insights from the provided consolidated account statement.

import argparse
import getpass
import json
import logging
import subprocess
import sys
import casparser

import pandas as pd

from datetime import datetime
from functools import wraps
from pathlib import Path

import pandas as pd
import requests
from colorama import Fore, init
from tabulate import tabulate

from pyxirr import xirr
from datetime import datetime, timedelta

import os

init()

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def log_function_call(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger.debug("Entering %s", func.__qualname__)
        try:
            return func(*args, **kwargs)
        except Exception:
            logger.exception("Exception in %s", func.__qualname__)
            raise
        finally:
            logger.debug("Exiting %s", func.__qualname__)

    return wrapper


# Constants
VERSION = "1.0.0"
NAV_API_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
FUND_HOUSE_CONCENTRATION_LIMIT_PERCENT = 25
NIFTY50_ISIN = 'INF179K01WM1'
ISIN_FILE = "config/isin.db"
GDP_DATA_FILE = "config/gdp_inflation_info.json"
NIFTY50_DATA_FILE = "config/hdfc_nifty50_nav.json"

os.environ["CASPARSER_ISIN_DB"] = ISIN_FILE

@log_function_call
def is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


@log_function_call
def parse_statement(pdf_file: Path, password: str) -> dict:
    if not pdf_file.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {pdf_file}")

    json_str = casparser.read_cas_pdf(str(pdf_file), password, output="json")
    return json.loads(json_str)
            

@log_function_call
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate redemption capital gains.",
        epilog=(
            "Examples:\n"
            "  python vivekaḥ.py help\n"
            "  python vivekaḥ.py version\n"
            '  python vivekaḥ.py list --pdf "statement.pdf"\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    modes = parser.add_subparsers(dest="mode", required=True)

    help_parser = modes.add_parser("help", help="Explain all modes and parameters.")
    help_parser.set_defaults(action="help")

    version_parser = modes.add_parser("version", help="Provides the version of this executable.")
    version_parser.set_defaults(action="version")

    list_parser = modes.add_parser(
        "list", help="List scheme names and AMFI codes from a password-protected CAS PDF."
    )
    add_statement_arguments(list_parser)
    list_parser.set_defaults(action="list")

    return parser

@log_function_call
def add_statement_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pdf",
        required=True,
        type=Path,
        help="Path to the consolidated account statement PDF.",
    )
    parser.add_argument(
        "--password",
        help="PDF password. If omitted, it will be requested without echoing.",
    )

@log_function_call
def load_statement(cas_detailed_json: dict) -> pd.DataFrame:
    print(Fore.GREEN + f"\r* {"LOAD STATEMENT":40.40}\033[K", end="", flush=True)
    columns = [
        "isin",
        "amfi_scheme_code",
        "investor_name",
        "date",
        "amount",
        "units",
        "nav",
        "balance",
        "type"
    ]    
    transaction_rows = []
    investor_name = cas_detailed_json["investor_info"]["name"]
    cas_detailed_folios = cas_detailed_json["folios"]
    for cas_detailed_folio in cas_detailed_folios:
        cas_detailed_schemes = cas_detailed_folio["schemes"]
        for cas_detailed_scheme in cas_detailed_schemes:
            asset_type = cas_detailed_scheme["type"]
            if asset_type == 'EQUITY':
                isin = cas_detailed_scheme["isin"]
                amfi = cas_detailed_scheme["amfi"]
                cas_detailed_transactions = cas_detailed_scheme["transactions"]
                for index, cas_detailed_transaction in enumerate(cas_detailed_transactions):
                    next_cas_detailed_transaction = None
                    if index < len(cas_detailed_transactions) - 1:
                        next_cas_detailed_transaction = cas_detailed_transactions[index + 1]                            
                    tx_date = cas_detailed_transaction["date"]        
                    tx_amount = cas_detailed_transaction["amount"]
                    tx_units = cas_detailed_transaction["units"]
                    tx_nav = cas_detailed_transaction["nav"]
                    tx_balance = cas_detailed_transaction["balance"]
                    tx_type = cas_detailed_transaction["type"]
                    if tx_type == "PURCHASE" or tx_type == "PURCHASE_SIP" or tx_type == "SWITCH_IN" or tx_type == 'SWITCH_IN_MERGER':
                        tx_amount = float(tx_amount)
                        if next_cas_detailed_transaction is not None and next_cas_detailed_transaction["type"] == "STAMP_DUTY_TAX":
                            tx_amount = tx_amount + float(next_cas_detailed_transaction["amount"])
                        tx_units = float(tx_units)
                        transaction_rows.append(
                                [
                                    isin,
                                    amfi,
                                    investor_name,
                                    datetime.strptime(tx_date, "%Y-%m-%d").date(),
                                    tx_amount,
                                    tx_units,
                                    tx_nav,
                                    tx_balance,
                                    "PURCHASE"
                                ]
                            )                        
                    elif tx_type == "REDEMPTION" or tx_type == 'REVERSAL' or tx_type == 'SWITCH_OUT' or tx_type == 'GIFT_OUT' or tx_type == 'DIVIDEND_PAYOUT':
                        tx_amount = float(tx_amount)
                        if tx_type == 'DIVIDEND_PAYOUT':
                            tx_units = 0
                        else:
                            tx_units = float(tx_units)    
                        if next_cas_detailed_transaction is not None and next_cas_detailed_transaction["type"] == "STT_TAX":
                            tx_amount = tx_amount - float(next_cas_detailed_transaction["amount"])                                                                
                        transaction_rows.append(
                            [
                                isin,
                                amfi,
                                investor_name,
                                datetime.strptime(tx_date, "%Y-%m-%d").date(),
                                tx_amount,
                                tx_units,
                                tx_nav,
                                tx_balance,
                                "SELL"
                            ]
                        )
                    elif tx_type == "STAMP_DUTY_TAX" or tx_type == 'STT_TAX' or tx_type == 'MISC':
                        pass
                    else:
                        print(Fore.RED + "Found a unsupported tx_type " + tx_type)
    print(Fore.GREEN + f"\r* {"LOAD STATEMENT":40.40}| Total transactions = {len(transaction_rows)}\033[K", end="\n", flush=True)
    temp_df = pd.DataFrame(transaction_rows, columns=columns)
    temp_df = temp_df.astype({
        "isin": "string",
        "amfi_scheme_code": "string",
        "investor_name": "string",
        "date": "datetime64[ns]",
        "amount": "float64",
        "units": "float64",
        "nav": "float64",
        "balance": "float64",
        "type": "string"        
    })
    return temp_df

@log_function_call
def get_latest_nav_for_all_schemes() -> pd.DataFrame:
    print(Fore.GREEN + f"\r* {"FETCHING NAV":40.40}\033[K", end="", flush=True)
    columns = [
        "isin",
        "fund_house",
        "scheme_name",
        "plan",
        "option",
        "nav"
    ]     
    nav_rows = []
    try:
        r = requests.get(NAV_API_URL, timeout = 20)
        split = r.text.split("\r\n")
        latest_fund_house_name = None
        for line in split:
            code = line.split(";")
            if code is not None and len(code) ==1 and code[0].endswith("Mutual Fund"):
                latest_fund_house_name = code[0]
            if code is not None and len(code) == 8 and code[1] is not None and code[1].startswith("INF") and code[6] is not None and is_float(code[6]):
                schemeISIN = code[1]
                schemeName = code[3]
                schemePlan = code[4]
                schemeOption = code[5]
                schemeNAV = code[6]    
                nav_rows.append([schemeISIN, latest_fund_house_name, schemeName, schemePlan, schemeOption, schemeNAV])
        print(Fore.GREEN + f"\r* {"FETCHING NAV":40.40}| Total schemes = {len(nav_rows)}\033[K", end="\n", flush=True)
        return pd.DataFrame(nav_rows, columns=columns)
    except Exception as e:  
        logger.exception("Exception occured:{}".format(e))
        print(Fore.RED + f"\r* {'MUTUAL FUND':40.40}| NAV Update failed\033[K", end="\n", flush=True)
        sys.exit(-1)

@log_function_call
def compute_full_xirr(cas_detailed_json, cas_detailed_json_df):
    print(Fore.GREEN + f"\r* {"COMPUTE XIRR":40.40}\033[K", end="", flush=True)

    cas_detailed_json_df_temp = cas_detailed_json_df[['date', 'amount']].copy()

    counter = 0
    cas_detailed_folios = cas_detailed_json["folios"]
    for cas_detailed_folio in cas_detailed_folios:
        cas_detailed_schemes = cas_detailed_folio["schemes"]
        for cas_detailed_scheme in cas_detailed_schemes:
            asset_type = cas_detailed_scheme["type"]
            if asset_type == 'EQUITY':
                cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                cas_summary_valudate = cas_detailed_scheme["valuation"]["date"]
                cas_detailed_json_df_temp.loc[len(cas_detailed_json_df_temp)] = \
                    [datetime.strptime(cas_summary_valudate, "%Y-%m-%d").date(), float(cas_summary_marketvalue.replace(',',''))*-1]
                counter += 1

    computed_xirr = round(xirr(cas_detailed_json_df_temp['date'].tolist(), cas_detailed_json_df_temp['amount'].tolist())*100,2)
    print(Fore.GREEN + f"\r* {"COMPUTE XIRR":40.40}| XIRR = {computed_xirr}\033[K", end="\n", flush=True)

@log_function_call
def compute_schemewise_xirr(cas_detailed_json, cas_detailed_json_df):

    print(Fore.GREEN + f"\r* {"COMPUTE SCHEMEWISE XIRR":40.40}\033[K", end="\n", flush=True)

    cas_detailed_json_df_scheme_all = cas_detailed_json_df[['isin','date', 'amount']].copy()

    cas_detailed_folios = cas_detailed_json["folios"]
    for cas_detailed_folio in cas_detailed_folios:
        cas_detailed_schemes = cas_detailed_folio["schemes"]
        for cas_detailed_scheme in cas_detailed_schemes:
            asset_type = cas_detailed_scheme["type"]
            isin = cas_detailed_scheme["isin"]
            scheme = cas_detailed_scheme["scheme"]
            if asset_type == 'EQUITY':
                cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                cas_summary_valudate = cas_detailed_scheme["valuation"]["date"]
                market_value = float(cas_summary_marketvalue.replace(',',''))
                market_date = datetime.strptime(cas_summary_valudate, "%Y-%m-%d").date()
                if market_value > 0:
                    cas_detailed_json_df_scheme = cas_detailed_json_df_scheme_all[cas_detailed_json_df_scheme_all['isin'] == isin].copy()                    
                    cas_detailed_json_df_scheme.loc[len(cas_detailed_json_df_scheme)] = \
                        [isin, pd.Timestamp(market_date) , float(market_value)*-1]
                    computed_xirr = round(xirr(cas_detailed_json_df_scheme['date'].tolist(), cas_detailed_json_df_scheme['amount'].tolist())*100,2)
                    print(Fore.GREEN + f"  {' ':40.40}| Scheme {scheme:60.60}| XIRR = {computed_xirr}")

@log_function_call
def search_for_regular_plans(cas_detailed_json, schemewise_latest_nav):
    print(Fore.GREEN + f"\r* {"FUND SCHEME TYPE":40.40}\033[K", end="", flush=True)
    count = 0
    cas_detailed_folios = cas_detailed_json["folios"]
    for cas_detailed_folio in cas_detailed_folios:
        cas_detailed_schemes = cas_detailed_folio["schemes"]
        for cas_detailed_scheme in cas_detailed_schemes:
            asset_type = cas_detailed_scheme["type"]
            isin = cas_detailed_scheme["isin"]
            if asset_type == 'EQUITY':
                cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                market_value = float(cas_summary_marketvalue.replace(',',''))
                if market_value > 0:
                    amfi_scheme_plan = schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]['plan'].values[0] if len(schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]) > 0 else None
                    isRegular = True
                    if amfi_scheme_plan.upper() == "DIRECT PLAN":
                            isRegular = False
                    if isRegular:
                        count += 1

    if count == 0:
        print(Fore.GREEN + f"\r* {"FUND SCHEME TYPE":40.40}| GOOD - No Regular Plans\033[K", end="\n", flush=True)
    else:
        print(Fore.YELLOW + f"\r* {"FUND SCHEME TYPE":40.40}| {count} Regular Plans Found. Higher Expense ratios\033[K", end="\n", flush=True)

@log_function_call
def check_dividend_plan_funds(cas_detailed_json, schemewise_latest_nav):
    print(Fore.GREEN + f"\r* {"FUND PLAN TYPE":40.40}\033[K", end="", flush=True)
    count = 0
    cas_detailed_folios = cas_detailed_json["folios"]
    for cas_detailed_folio in cas_detailed_folios:
        cas_detailed_schemes = cas_detailed_folio["schemes"]
        for cas_detailed_scheme in cas_detailed_schemes:
            asset_type = cas_detailed_scheme["type"]
            isin = cas_detailed_scheme["isin"]

            if asset_type == 'EQUITY':
                cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                market_value = float(cas_summary_marketvalue.replace(',',''))
                if market_value > 0:
                    amfi_scheme_option = schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]['option'].values[0] if len(schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]) > 0 else None
                    if not amfi_scheme_option.upper().startswith("GROWTH"):
                        count += 1

    if count == 0:
        print(Fore.GREEN + f"\r* {"FUND PLAN TYPE":40.40}| GOOD - No Dividend Plans\033[K", end="\n", flush=True)
    else:
        print(Fore.YELLOW + f"\r* {"FUND PLAN TYPE":40.40}| {count} Dividend Funds Found. Avoid such funds as a long term investor\033[K", end="\n", flush=True)

@log_function_call
def check_optimum_funds(cas_detailed_json):
    print(Fore.GREEN + f"\r* {"FUND COUNT":40.40}\033[K", end="", flush=True)
    count = 0
    cas_detailed_folios = cas_detailed_json["folios"]
    for cas_detailed_folio in cas_detailed_folios:
        cas_detailed_schemes = cas_detailed_folio["schemes"]
        for cas_detailed_scheme in cas_detailed_schemes:
            asset_type = cas_detailed_scheme["type"]
            scheme = cas_detailed_scheme["scheme"]
            if asset_type == 'EQUITY':
                cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                market_value = float(cas_summary_marketvalue.replace(',',''))
                if market_value > 0:
                    count += 1

    if count <= 6:
        print(Fore.GREEN + f"\r* {"FUND COUNT":40.40}| GOOD - Optimum 5-6 Funds in Equity portfolio\033[K", end="\n", flush=True)
    else:
        print(Fore.YELLOW + f"\r* {"FUND COUNT":40.40}| {count} Funds in Equity portfolio. Consider reducing the total number of funds to 5-6 \033[K", end="\n", flush=True)

@log_function_call    
def check_fund_house_concentration(cas_detailed_json, schemewise_latest_nav):
    print(Fore.GREEN + f"\r* {"FUND HOUSE CONCENTRATION":40.40}\033[K", end="", flush=True)
    total_market_value = 0
    map_of_fund_house_to_market_value = {}
    cas_detailed_folios = cas_detailed_json["folios"]
    for cas_detailed_folio in cas_detailed_folios:
        cas_detailed_schemes = cas_detailed_folio["schemes"]
        for cas_detailed_scheme in cas_detailed_schemes:
            asset_type = cas_detailed_scheme["type"]
            isin = cas_detailed_scheme["isin"]

            if asset_type == 'EQUITY':
                cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                market_value = float(cas_summary_marketvalue.replace(',',''))
                if market_value > 0:
                    amfi_scheme_fund_house = schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]['fund_house'].values[0] if len(schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]) > 0 else None
                    if amfi_scheme_fund_house in map_of_fund_house_to_market_value:
                        map_of_fund_house_to_market_value[amfi_scheme_fund_house] += market_value
                    else:
                        map_of_fund_house_to_market_value[amfi_scheme_fund_house] = market_value
                    total_market_value += market_value
    fund_houses_concentration_list = []
    for fund_house, market_value in map_of_fund_house_to_market_value.items():
        if (market_value / total_market_value) > (FUND_HOUSE_CONCENTRATION_LIMIT_PERCENT/100):
            fund_houses_concentration_list.append(fund_house)

    if len(fund_houses_concentration_list) == 0:
        print(Fore.GREEN + f"\r* {"FUND HOUSE CONCENTRATION":40.40}| GOOD - No High Concentration\033[K", end="\n", flush=True)
    else:
        print(Fore.YELLOW + f"\r* {"FUND HOUSES":40.40}| {fund_houses_concentration_list} High Concentration (>{FUND_HOUSE_CONCENTRATION_LIMIT_PERCENT}%) Found. Consider diversifying\033[K", end="\n", flush=True)   

@log_function_call
def compare_with_index(cas_detailed_json, statement_df, schemewise_latest_nav):
    print(Fore.GREEN + f"\r* {"INDEX COMPARISON":40.40}\033[K", end="\n", flush=True)

    # Get Current Valuation
    total_market_value = 0
    cas_detailed_folios = cas_detailed_json["folios"]
    for cas_detailed_folio in cas_detailed_folios:
        cas_detailed_schemes = cas_detailed_folio["schemes"]
        for cas_detailed_scheme in cas_detailed_schemes:
            asset_type = cas_detailed_scheme["type"]
            if asset_type == 'EQUITY':
                cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                market_value = float(cas_summary_marketvalue.replace(',',''))
                total_market_value += market_value 
    print(Fore.GREEN + f"  {' ':40.40}| Total Market Value     = {round(total_market_value,0)}")   
    print(Fore.GREEN + f"  {' ':40.40}| Total Invested Amount  = {round(statement_df['amount'].sum(),0)}")   

    # Load the GDP Inflation Information
    df_gdp_inflation_info = pd.read_json(GDP_DATA_FILE)
    df_nifty50_info = pd.read_json(NIFTY50_DATA_FILE, dtype=False, convert_dates=False)
    df_nifty50_info["date"] = pd.to_datetime(df_nifty50_info["date"], format="%d-%m-%Y")

    df_gdp_inflation_info_sorted = df_gdp_inflation_info.sort_values(by="year", ascending=False)
    df_gdp_inflation_info_sorted['total_value'] = 0.0
    df_gdp_inflation_info_sorted['cagr'] = 0.0
    cagr = 1.0
    for row in df_gdp_inflation_info_sorted.itertuples():
        inflation_plus_gdp = (row.inflation or 0) + (row.gdp or 0)
        cagr = cagr * ((float(inflation_plus_gdp) / 100.0) + 1.0)
        df_gdp_inflation_info_sorted.at[row.Index, 'cagr'] = cagr

    # Fill Contribution amount
    nifty50_units_sum = 0.0
    for row in statement_df.itertuples():
        contributiondate = row.date
        contributionamount = row.amount
        year = contributiondate.year if hasattr(contributiondate, 'year') else int(str(contributiondate)[:4])
        df_gdp_inflation_info_sorted.loc[df_gdp_inflation_info_sorted['year'] == year, 'total_value'] += float(contributionamount)
        nifty50_nav = df_nifty50_info[df_nifty50_info['date'] == contributiondate.strftime("%Y-%m-%d")]
        if nifty50_nav.empty:
            window = 5  # Number of days before and after
            nearby_dates = [
                contributiondate + timedelta(days=i) 
                for i in range(-window, window + 1)
            ]

            for nearby_date in nearby_dates:            
                nifty50_nav_nearby = df_nifty50_info[df_nifty50_info['date'] == nearby_date.strftime("%Y-%m-%d")]
                if nifty50_nav_nearby.empty:
                    continue
                else:
                    nifty50_units_sum += contributionamount/(float(nifty50_nav_nearby.iloc[0].nav))
                    break
        else:
            nifty50_units_sum += contributionamount/(float(nifty50_nav.iloc[0].nav))
    nifty50_nav = schemewise_latest_nav[schemewise_latest_nav['isin'] == NIFTY50_ISIN]['nav'].values[0] if len(schemewise_latest_nav[schemewise_latest_nav['isin'] == NIFTY50_ISIN]) > 0 else 0
    nifty50_corpus = float(nifty50_nav) * nifty50_units_sum
    print(Fore.GREEN + f"  {' ':40.40}| Total NIFTY 50 Indexed Amount   = {round(nifty50_corpus,0)}")   

    total_indexed_amount = 0.0
    for row in df_gdp_inflation_info_sorted.itertuples():
        total_indexed_amount += row.total_value * row.cagr
    print(Fore.GREEN + f"  {' ':40.40}| Total GDP + Inflation Indexed Amount   = {round(total_indexed_amount,0)}")   

@log_function_call
def provide_insights(statement: dict) -> None:
    statement_df = load_statement(statement)
    schemewise_latest_nav = get_latest_nav_for_all_schemes()
    compute_full_xirr(statement, statement_df)
    compute_schemewise_xirr(statement, statement_df)
    search_for_regular_plans(statement, schemewise_latest_nav)
    check_optimum_funds(statement)
    check_dividend_plan_funds(statement, schemewise_latest_nav)
    check_fund_house_concentration(statement, schemewise_latest_nav)
    compare_with_index(statement, statement_df, schemewise_latest_nav)

def print_banner() -> None:
    print(
        f"विवेकः - Portfolio Insights v{VERSION}\n"
        "Author: Balaji Sundaresan\n"
        "Provide Portfolio Insights from consolidated account statements.\n"
    )


@log_function_call
def main(argv: list[str] | None = None) -> int:
    print_banner()
    parser = build_parser()
    args = parser.parse_args(argv)
    logger.info(
        "Command line parameters: mode=%s, pdf=%s",
        args.mode,
        getattr(args, "pdf", None),
    )

    if args.action == "help":
        parser.print_help()
        print(
            "\nModes:\n"
            "  help    Show this help and explain all parameters.\n"
            "  list    Require --pdf; accept --password or prompt for it; "
            "List insights from the provided consolidated account statement.\n"
            "Parameters:\n"
            "  --pdf PATH       Required PDF statement path for list and redeem.\n"
            "  --password TEXT  Optional PDF password. If omitted, the program "
            "prompts without echoing the password.\n"
        )
        return 0

    if args.action == "version":
        print("Version = " + VERSION)
        return 0

    password = args.password
    if password is None:
        password = getpass.getpass("PDF password: ")

    try:
        statement = parse_statement(args.pdf, password)
        if args.action == "list":
            provide_insights(statement)
    except (FileNotFoundError, OSError, subprocess.CalledProcessError, ValueError, RuntimeError) as error:
        print(Fore.RED + f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
