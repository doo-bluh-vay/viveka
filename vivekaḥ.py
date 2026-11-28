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
VERSION = "0.0.4"
NAV_API_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"
FUND_HOUSE_CONCENTRATION_LIMIT_PERCENT = 25
NIFTY50_ISIN = 'INF179K01WM1'
FLEXICAP1_ISIN = 'INF179K01UT0'
FLEXICAP2_ISIN = 'INF090I01FK3'
ISIN_FILE = "config/isin.db"
GDP_DATA_FILE = "config/gdp_inflation_info.json"
NIFTY50_DATA_FILE = "config/hdfc_nifty50_nav.json"
FLEXICAP1_DATA_FILE = "config/hdfc_flexicap_nav.json"
FLEXICAP2_DATA_FILE = "config/franklintempleton_flexicap_nav.json"
XIRR_ASSET_TYPES = ['EQUITY','DEBT','GOLD']
OPTIMUM_FUNDS = [5,2,2]

# Globals
os.environ["CASPARSER_ISIN_DB"] = ISIN_FILE
map_classified_funds = {}

@log_function_call
def is_float(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False

@log_function_call
def classify_funds(dict_of_investor_statements):
    investor_names = dict_of_investor_statements.keys()
    for investor_name in dict_of_investor_statements:
        cas_detailed_json,temp_df = dict_of_investor_statements[investor_name]
        cas_detailed_folios = cas_detailed_json["folios"]
        for cas_detailed_folio in cas_detailed_folios:
            cas_detailed_schemes = cas_detailed_folio["schemes"]
            for cas_detailed_scheme in cas_detailed_schemes:
                scheme_name = cas_detailed_scheme["scheme"] 
                asset_type = cas_detailed_scheme["type"]
                isin = cas_detailed_scheme["isin"]
                if isin is not None:
                    if 'GOLD' in scheme_name.upper():
                        map_classified_funds[isin] = {
                                                        'ASSET_TYPE':'GOLD',
                                                        'DEFICIENT_DATA':dict.fromkeys(investor_names, False),
                                                        'ACTIVE_FUND':dict.fromkeys(investor_names, False)
                                                     }
                    else:
                        map_classified_funds[isin] = {
                                                        'ASSET_TYPE':asset_type,
                                                        'DEFICIENT_DATA':dict.fromkeys(investor_names, False),
                                                        'ACTIVE_FUND':dict.fromkeys(investor_names, False)
                                                     }

@log_function_call    
def check_data_deficiency(dict_of_investor_statements):
    for investor_name in dict_of_investor_statements:
        print(Fore.GREEN + f"\r* {"DATA DEFICIENCY CHECK":40.40}| {investor_name}\033[K", end="\n", flush=True)
        statement, cas_detailed_json_df = dict_of_investor_statements[investor_name]
        data_deficiency_count = 0
        for isin in cas_detailed_json_df['isin'].unique():
            if isin is not None:
                cas_detailed_json_df_isin = cas_detailed_json_df[cas_detailed_json_df['isin'] == isin].copy()
                if cas_detailed_json_df_isin.empty == True:
                    continue
                cas_detailed_json_df_isin_sorted = cas_detailed_json_df_isin.sort_values(by='date').copy()
                cas_detailed_json_df_isin_sorted = cas_detailed_json_df_isin_sorted[~(cas_detailed_json_df_isin_sorted['type'] == 'DIVIDEND_PAYOUT')]
                scheme_name = cas_detailed_json_df_isin_sorted.at[cas_detailed_json_df_isin_sorted.index[0], 'scheme_name']
                transaction_rows = []
                for tx_date in cas_detailed_json_df_isin_sorted['date'].unique():
                    cas_detailed_json_df_isin_date = cas_detailed_json_df_isin_sorted[cas_detailed_json_df_isin_sorted['date'] == tx_date].copy()
                    total_units = 0
                    list_balances = []
                    list_types = []
                    for row in cas_detailed_json_df_isin_date.itertuples():
                        tx_type = row.type
                        total_units += row.units
                        list_balances.append(row.balance)
                        list_types.append(tx_type)
                    transaction_rows.append(
                            [
                                tx_date,
                                total_units,
                                list_balances,
                                list_types
                            ]
                        )           
                if len(transaction_rows) > 0:             
                    idx = 0
                    old_tx_balance = 0
                    while idx < len(transaction_rows):
                        tx_date = transaction_rows[idx][0]
                        total_units = transaction_rows[idx][1]
                        list_of_balances = transaction_rows[idx][2]
                        list_of_types = transaction_rows[idx][3]
                        if idx == 0:
                            if not ( any(item in ["PURCHASE","PURCHASE_SIP","SWITCH_IN",'SWITCH_IN_MERGER'] for item in list_of_types)):
                                print(Fore.RED + f"{' ':40.40}    '{scheme_name}' : FIRST ENTRY MUST BE A PURCHASE")
                                map_classified_funds[isin]['DEFICIENT_DATA'][investor_name] = True
                                data_deficiency_count += 1
                                break
                            elif total_units not in list_of_balances:
                                print(Fore.RED + f"{' ':40.40}    '{scheme_name}' : INCORRECT STARTING BALANCE {list_of_balances} ON DATE {tx_date}")
                                map_classified_funds[isin]['DEFICIENT_DATA'][investor_name] = True
                                data_deficiency_count += 1
                                break                
                            old_tx_balance = max(list_of_balances)
                        else:
                            new_balance = round(total_units + old_tx_balance,3)                        
                            if new_balance not in list_of_balances:
                                print(Fore.RED + f"{' ':40.40}    '{scheme_name}' : INCORRECT BALANCE {list_of_balances} EXPECTED {new_balance} ON DATE {tx_date}")
                                map_classified_funds[isin]['DEFICIENT_DATA'][investor_name] = True
                                data_deficiency_count += 1
                                break
                            old_tx_balance = new_balance  
                        idx += 1
        if (data_deficiency_count == 0):
            print(Fore.GREEN + f"\r  {" ":40.40}| CHECKS PASSED\033[K", end="\n", flush=True)
    
@log_function_call    
def check_active_funds(dict_of_investor_statements):
    print(Fore.GREEN + f"\r* {"ACTIVE FUNDS CHECK":40.40}\033[K", end="", flush=True)

    isin_list = map_classified_funds.keys()
    investor_name_list = dict_of_investor_statements.keys()
    for isin in isin_list:
        for investor_name in investor_name_list:
            cas_detailed_json, cas_detailed_json_df = dict_of_investor_statements[investor_name]        
            cas_detailed_folios = cas_detailed_json["folios"]
            isin_found = False
            for cas_detailed_folio in cas_detailed_folios:
                cas_detailed_schemes = cas_detailed_folio["schemes"]
                for cas_detailed_scheme in cas_detailed_schemes:
                    statement_isin = cas_detailed_scheme["isin"]
                    if statement_isin != isin:
                        continue
                    isin_found = True
                    if len(cas_detailed_scheme["transactions"]) > 0:
                        cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                        market_value = float(cas_summary_marketvalue.replace(',',''))
                        if market_value > 0:
                            map_classified_funds[isin]['ACTIVE_FUND'][investor_name] = True
                    break
                if isin_found:
                    break
    
@log_function_call
def parse_statement(pdf_files: list, password: str) -> list[dict]:
    list_of_statements = []
    for pdf_file in pdf_files:
        if not pdf_file.is_file():
            raise FileNotFoundError(f"PDF file does not exist: {pdf_file}")

        json_str = casparser.read_cas_pdf(str(pdf_file), password, output="json")
        list_of_statements.append(json.loads(json_str))
    return list_of_statements

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
        "-f",
        "--files",
        nargs="+",
        type=Path,
        required=True,
        help="Comma-separated list of consolidated account statement PDF filenames (e.g., investor1.pdf,investor2.pdf)",
    )    
    parser.add_argument(
        "--password",
        help="PDF password. If omitted, it will be requested without echoing.",
    )

@log_function_call
def load_statement(cas_detailed_jsons: list) -> dict[str, pd.DataFrame]:
    dict_of_investor_statements = {}
    print(Fore.GREEN + f"\r* {"LOAD STATEMENT":40.40}\033[K", end="", flush=True)
    try:
        for cas_detailed_json in cas_detailed_jsons:
            transaction_rows = []
            investor_name = cas_detailed_json["investor_info"]["name"]
            cas_detailed_folios = cas_detailed_json["folios"]
            for cas_detailed_folio in cas_detailed_folios:
                cas_detailed_schemes = cas_detailed_folio["schemes"]
                for cas_detailed_scheme in cas_detailed_schemes:
                    scheme_name = cas_detailed_scheme["scheme"] 
                    asset_type = cas_detailed_scheme["type"]
                    isin = cas_detailed_scheme["isin"]
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
                        if tx_type in ["PURCHASE","PURCHASE_SIP","SWITCH_IN",'SWITCH_IN_MERGER','DIVIDEND_REINVEST','GIFT_IN']:
                            tx_amount = float(tx_amount)
                            if next_cas_detailed_transaction is not None and next_cas_detailed_transaction["type"] == "STAMP_DUTY_TAX":
                                tx_amount = tx_amount + float(next_cas_detailed_transaction["amount"])
                            tx_units = float(tx_units)
                            transaction_rows.append(
                                    [
                                        isin,
                                        scheme_name,
                                        investor_name,
                                        datetime.strptime(tx_date, "%Y-%m-%d").date(),
                                        tx_amount,
                                        tx_units,
                                        tx_nav,
                                        tx_balance,
                                        tx_type,
                                        asset_type
                                    ]
                                )                        
                        elif tx_type in ["REDEMPTION","REVERSAL","SWITCH_OUT",'SWITCH_OUT_MERGER','GIFT_OUT','DIVIDEND_PAYOUT']:
                            tx_amount = float(tx_amount)
                            if tx_type == 'DIVIDEND_PAYOUT':
                                tx_units = tx_nav = tx_balance = 0
                            else:
                                tx_units = float(tx_units)    
                            if next_cas_detailed_transaction is not None and next_cas_detailed_transaction["type"] == "STT_TAX":
                                tx_amount = tx_amount - float(next_cas_detailed_transaction["amount"])                                                                
                            transaction_rows.append(
                                [
                                    isin,
                                    scheme_name,
                                    investor_name,
                                    datetime.strptime(tx_date, "%Y-%m-%d").date(),
                                    tx_amount,
                                    tx_units,
                                    tx_nav,
                                    tx_balance,
                                    tx_type,
                                    asset_type
                                ]
                            )
                        elif tx_type == "STAMP_DUTY_TAX" or tx_type == 'STT_TAX' or tx_type == 'MISC' or tx_type == 'TDS_TAX':
                            pass
                        else:
                            print(Fore.RED + "Found a unsupported tx_type " + tx_type)
            print(Fore.GREEN + f"\r* {"LOAD STATEMENT":40.40}| Total transactions for {investor_name} = {len(transaction_rows)}\033[K", end="\n", flush=True)
            column_info = {
                "isin": "string",
                "scheme_name": "string",
                "investor_name": "string",
                "date": "datetime64[ns]",
                "amount": "float64",
                "units": "float64",
                "nav": "float64",
                "balance": "float64",
                "type": "string"     ,
                "asset_type": "string"   
            }        
            temp_df = pd.DataFrame(transaction_rows, columns=list(column_info.keys()))
            temp_df = temp_df.astype(column_info)
            dict_of_investor_statements[investor_name] = (cas_detailed_json,temp_df)
    except Exception as e:
        logger.exception(f"EXCEPTION: {type(e).__name__}, {e.args}, {str(e)}")
        print(Fore.RED + f"\r* {"LOAD STATEMENT":40.40}| Loading the Consolidated Statement Failed\033[K", end="\n", flush=True)
        sys.exit(-1)
    return dict_of_investor_statements

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
        idx = 0
        header = 'Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date'
        for line in split:
            if idx == 0:
                if header != line:
                    raise Exception("NAV Data Header Mismatch")
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

            idx+=1
        print(Fore.GREEN + f"\r* {"FETCHING NAV":40.40}| Total schemes = {len(nav_rows)}\033[K", end="\n", flush=True)
        return pd.DataFrame(nav_rows, columns=columns)
    except Exception as e:  
        logger.exception(f"EXCEPTION: {type(e).__name__}, {e.args}, {str(e)}")
        print(Fore.RED + f"\r* {'MUTUAL FUND':40.40}| NAV Update failed\033[K", end="\n", flush=True)
        sys.exit(-1)

@log_function_call
def compute_full_xirr(dict_of_investor_statements):
    print(Fore.GREEN + f"\r* {"COMPUTE XIRR (EXCL DEFICIENT DATA FUNDS)":40.40}\033[K", end="\n", flush=True)

    try:
        for xirr_asset_type in XIRR_ASSET_TYPES:        
            total_counter = 0
            xirr_date_list = []
            xirr_value_list = []
            for investor_name in dict_of_investor_statements:
                cas_detailed_json, cas_detailed_json_df = dict_of_investor_statements[investor_name]        
                counter = 0
                list_of_funds_with_data = []
                for isin in map_classified_funds:
                    if  map_classified_funds[isin]['ASSET_TYPE'] == xirr_asset_type and map_classified_funds[isin]['DEFICIENT_DATA'][investor_name] == False:
                        list_of_funds_with_data.append(isin)
                cas_detailed_json_df_copy = cas_detailed_json_df[['isin','date', 'amount']].copy()
                cas_detailed_json_df_temp = cas_detailed_json_df_copy[cas_detailed_json_df_copy['isin'].isin(list_of_funds_with_data)].copy()
                cas_detailed_folios = cas_detailed_json["folios"]
                for cas_detailed_folio in cas_detailed_folios:
                    cas_detailed_schemes = cas_detailed_folio["schemes"]
                    for cas_detailed_scheme in cas_detailed_schemes:
                        isin = cas_detailed_scheme["isin"]
                        if isin in list_of_funds_with_data and len(cas_detailed_scheme["transactions"]) > 0:
                            cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                            cas_summary_valudate = cas_detailed_scheme["valuation"]["date"]
                            if float(cas_summary_marketvalue.replace(',','')) > 0 :
                                new_row = {'isin': isin, 'date': pd.Timestamp(cas_summary_valudate), 'amount' : float(cas_summary_marketvalue.replace(',',''))*-1}
                                cas_detailed_json_df_temp = pd.concat([cas_detailed_json_df_temp, pd.DataFrame([new_row])], ignore_index=True)                            
                                total_counter += 1
                                counter += 1
                if counter > 0:
                    xirr_date_list.extend(cas_detailed_json_df_temp['date'].tolist())
                    xirr_value_list.extend( cas_detailed_json_df_temp['amount'].tolist())
            if total_counter > 0 :
                computed_xirr = round(xirr(xirr_date_list,xirr_value_list)*100,2)
                print(Fore.GREEN + f"\r  {" ":40.40}| {xirr_asset_type:10.10} XIRR = {computed_xirr}\033[K", end="\n", flush=True)
            else:
                print(Fore.GREEN + f"\r  {" ":40.40}| {xirr_asset_type:10.10} XIRR = No Funds Found\033[K", end="\n", flush=True)
    except Exception as e:  
        logger.exception(f"EXCEPTION: {type(e).__name__}, {e.args}, {str(e)}")
        print(Fore.GREEN + f"\r  {"COMPUTE XIRR":40.40}| XIRR Computation Failed\033[K", end="\n", flush=True)
        sys.exit(-1)        

@log_function_call
def compute_schemewise_xirr(dict_of_investor_statements):

    print(Fore.GREEN + f"\r* {"COMPUTE SCHEMEWISE XIRR":40.40}\033[K", end="\n", flush=True)

    isin_list = map_classified_funds.keys()
    investor_name_list = dict_of_investor_statements.keys()
    for isin in isin_list:
        xirr_date_list = []
        xirr_value_list = []        
        for investor_name in investor_name_list:
            cas_detailed_json, cas_detailed_json_df = dict_of_investor_statements[investor_name]        
            cas_detailed_json_df_scheme_all = cas_detailed_json_df[['isin','date', 'amount']].copy()
            cas_detailed_folios = cas_detailed_json["folios"]
            isin_found = False
            for cas_detailed_folio in cas_detailed_folios:
                cas_detailed_schemes = cas_detailed_folio["schemes"]
                for cas_detailed_scheme in cas_detailed_schemes:
                    statement_isin = cas_detailed_scheme["isin"]
                    if statement_isin != isin:
                        continue
                    isin_found = True
                    scheme = cas_detailed_scheme["scheme"]                    
                    if map_classified_funds[isin]['DEFICIENT_DATA'][investor_name] == True:
                        print(Fore.RED + f"  {' ':40.40}| {scheme:60.60}| DEFICIENT DATA")
                    else:
                        if len(cas_detailed_scheme["transactions"]) > 0:
                            cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                            cas_summary_valudate = cas_detailed_scheme["valuation"]["date"]
                            market_value = float(cas_summary_marketvalue.replace(',',''))
                            market_date = datetime.strptime(cas_summary_valudate, "%Y-%m-%d").date()
                            if market_value > 0:
                                cas_detailed_json_df_scheme = cas_detailed_json_df_scheme_all[cas_detailed_json_df_scheme_all['isin'] == isin].copy()                    
                                cas_detailed_json_df_scheme.loc[len(cas_detailed_json_df_scheme)] = \
                                    [isin, pd.Timestamp(market_date) , float(market_value)*-1]
                                xirr_date_list.extend(cas_detailed_json_df_scheme['date'].tolist())
                                xirr_value_list.extend( cas_detailed_json_df_scheme['amount'].tolist())
                    break
                if isin_found:
                    break
        if len(xirr_date_list) > 0 and len(xirr_value_list) > 0:
            computed_xirr = round(xirr(xirr_date_list, xirr_value_list)*100,2)
            print(Fore.GREEN + f"  {' ':40.40}| {scheme:60.60}| XIRR = {computed_xirr}")

@log_function_call
def search_for_regular_plans(schemewise_latest_nav):
    print(Fore.GREEN + f"\r* {"FUND SCHEME TYPE":40.40}\033[K", end="", flush=True)
    count = 0
    isin_list = map_classified_funds.keys()
    for isin in isin_list:
        if True in map_classified_funds[isin]['ACTIVE_FUND'].values():
            temp_df = schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]
            if not temp_df.empty:
                amfi_scheme_plan = temp_df['plan'].values[0]
                if amfi_scheme_plan is not None:
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
def check_dividend_plan_funds(schemewise_latest_nav):
    print(Fore.GREEN + f"\r* {"FUND PLAN TYPE":40.40}\033[K", end="", flush=True)

    count = 0
    isin_list = map_classified_funds.keys()
    for isin in isin_list:
        if True in map_classified_funds[isin]['ACTIVE_FUND'].values():
            temp_df = schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]
            if not temp_df.empty:
                amfi_scheme_option = schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]['option'].values[0] if len(schemewise_latest_nav[schemewise_latest_nav['isin'] == isin]) > 0 else None
                if amfi_scheme_option is not None and (not amfi_scheme_option.upper().startswith("GROWTH")):
                    count += 1   
    if count == 0:
        print(Fore.GREEN + f"\r* {"FUND PLAN TYPE":40.40}| GOOD - No Dividend Plans\033[K", end="\n", flush=True)
    else:
        print(Fore.YELLOW + f"\r* {"FUND PLAN TYPE":40.40}| {count} Dividend Funds Found. Avoid such funds as a long term investor\033[K", end="\n", flush=True)

@log_function_call
def check_optimum_funds():
    print(Fore.GREEN + f"\r* {"FUND COUNT":40.40}\033[K", end="\n", flush=True)
    optimum_funds_counter = [0]  * len(XIRR_ASSET_TYPES)

    isin_list = map_classified_funds.keys()
    for isin in isin_list:
        if True in map_classified_funds[isin]['ACTIVE_FUND'].values():
            idx = XIRR_ASSET_TYPES.index(map_classified_funds[isin]['ASSET_TYPE'])
            optimum_funds_counter[idx] += 1

    idx = 0
    while idx < len(XIRR_ASSET_TYPES):
        counter = optimum_funds_counter[idx]
        xirr_asset_type = XIRR_ASSET_TYPES[idx]
        optimum_value = OPTIMUM_FUNDS[idx]
        if counter <= optimum_value:
            print(Fore.GREEN + f"\r  {" ":40.40}| GOOD - Optimum {optimum_value} Funds in {xirr_asset_type} portfolio\033[K", end="\n", flush=True)
        else:
            print(Fore.YELLOW + f"\r  {" ":40.40}| {counter} Funds in {xirr_asset_type} portfolio. Consider reducing the total number of funds to {optimum_value} \033[K", end="\n", flush=True)
        idx += 1

@log_function_call    
def check_fund_house_concentration(dict_of_investor_statements, schemewise_latest_nav):
    print(Fore.GREEN + f"\r* {"FUND HOUSE CONCENTRATION":40.40}\033[K", end="", flush=True)
    total_market_value = 0
    map_of_fund_house_to_market_value = {}
    investor_name_list = dict_of_investor_statements.keys()
    for investor_name in investor_name_list:
        cas_detailed_json, cas_detailed_json_df = dict_of_investor_statements[investor_name]        
        cas_detailed_folios = cas_detailed_json["folios"]
        for cas_detailed_folio in cas_detailed_folios:
            cas_detailed_schemes = cas_detailed_folio["schemes"]
            for cas_detailed_scheme in cas_detailed_schemes:
                isin = cas_detailed_scheme["isin"]
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
def compare_with_index(dict_of_investor_statements, schemewise_latest_nav):
    print(Fore.GREEN + f"\r* {"INDEX COMPARISON":40.40}\033[K", end="\n", flush=True)

    # Load the GDP Inflation Information
    df_gdp_inflation_info = pd.read_json(GDP_DATA_FILE)
    df_nifty50_info = pd.read_json(NIFTY50_DATA_FILE, dtype=False, convert_dates=False)
    df_nifty50_info["date"] = pd.to_datetime(df_nifty50_info["date"], format="%d-%m-%Y")
    df_flexicap1_info = pd.read_json(FLEXICAP1_DATA_FILE, dtype=False, convert_dates=False)
    df_flexicap1_info["date"] = pd.to_datetime(df_flexicap1_info["date"], format="%d-%m-%Y")
    df_flexicap2_info = pd.read_json(FLEXICAP2_DATA_FILE, dtype=False, convert_dates=False)
    df_flexicap2_info["date"] = pd.to_datetime(df_flexicap2_info["date"], format="%d-%m-%Y")

    df_gdp_inflation_info_sorted = df_gdp_inflation_info.sort_values(by="year", ascending=False)
    df_gdp_inflation_info_sorted['total_value'] = 0.0
    df_gdp_inflation_info_sorted['cagr'] = 0.0
    cagr = 1.0
    for row in df_gdp_inflation_info_sorted.itertuples():
        inflation_plus_gdp = (row.inflation or 0) + (row.gdp or 0)
        cagr = cagr * ((float(inflation_plus_gdp) / 100.0) + 1.0)
        df_gdp_inflation_info_sorted.at[row.Index, 'cagr'] = cagr

    # Get Current Valuation
    total_market_value = 0
    total_invested_amount = 0
    nifty50_units_sum = 0.0
    flexicap1_units_sum = 0.0
    flexicap2_units_sum = 0.0

    investor_name_list = dict_of_investor_statements.keys()
    for investor_name in investor_name_list:
        cas_detailed_json, cas_detailed_json_df = dict_of_investor_statements[investor_name]  

        list_of_funds_with_data = []
        for isin in map_classified_funds:
            if  map_classified_funds[isin]['ASSET_TYPE'] == 'EQUITY' and map_classified_funds[isin]['DEFICIENT_DATA'][investor_name] == False:
                list_of_funds_with_data.append(isin)
                            
        cas_detailed_folios = cas_detailed_json["folios"]
        for cas_detailed_folio in cas_detailed_folios:
            cas_detailed_schemes = cas_detailed_folio["schemes"]
            for cas_detailed_scheme in cas_detailed_schemes:
                isin = cas_detailed_scheme["isin"]
                if isin is not None and isin in list_of_funds_with_data and len(cas_detailed_scheme["transactions"]) > 0:
                    cas_summary_marketvalue = cas_detailed_scheme["valuation"]["value"]
                    market_value = float(cas_summary_marketvalue.replace(',',''))
                    total_market_value += market_value 
                    cas_detailed_transactions = cas_detailed_scheme["transactions"]
                    for cas_detailed_transaction in cas_detailed_transactions:
                        tx_amount = cas_detailed_transaction["amount"]
                        if tx_amount is not None:
                            total_invested_amount += float(tx_amount)

        for row in cas_detailed_json_df.itertuples():
            if row.isin is None or pd.isna(row.isin) or row.isin not in list_of_funds_with_data:
                continue
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
            flexicap1_nav = df_flexicap1_info[df_flexicap1_info['date'] == contributiondate.strftime("%Y-%m-%d")]
            if flexicap1_nav.empty:
                window = 5  # Number of days before and after
                nearby_dates = [
                    contributiondate + timedelta(days=i) 
                    for i in range(-window, window + 1)
                ]

                for nearby_date in nearby_dates:            
                    flexicap1_nav_nearby = df_flexicap1_info[df_flexicap1_info['date'] == nearby_date.strftime("%Y-%m-%d")]
                    if flexicap1_nav_nearby.empty:
                        continue
                    else:
                        flexicap1_units_sum += contributionamount/(float(flexicap1_nav_nearby.iloc[0].nav))
                        break
            else:
                flexicap1_units_sum += contributionamount/(float(flexicap1_nav.iloc[0].nav))
            flexicap2_nav = df_flexicap2_info[df_flexicap2_info['date'] == contributiondate.strftime("%Y-%m-%d")]
            if flexicap2_nav.empty:
                window = 5  # Number of days before and after
                nearby_dates = [
                    contributiondate + timedelta(days=i) 
                    for i in range(-window, window + 1)
                ]

                for nearby_date in nearby_dates:            
                    flexicap2_nav_nearby = df_flexicap2_info[df_flexicap2_info['date'] == nearby_date.strftime("%Y-%m-%d")]
                    if flexicap2_nav_nearby.empty:
                        continue
                    else:
                        flexicap2_units_sum += contributionamount/(float(flexicap2_nav_nearby.iloc[0].nav))
                        break
            else:
                flexicap2_units_sum += contributionamount/(float(flexicap2_nav.iloc[0].nav))

    nifty50_nav = schemewise_latest_nav[schemewise_latest_nav['isin'] == NIFTY50_ISIN]['nav'].values[0] if len(schemewise_latest_nav[schemewise_latest_nav['isin'] == NIFTY50_ISIN]) > 0 else 0
    nifty50_corpus = float(nifty50_nav) * nifty50_units_sum
    flexicap1_nav = schemewise_latest_nav[schemewise_latest_nav['isin'] == FLEXICAP1_ISIN]['nav'].values[0] if len(schemewise_latest_nav[schemewise_latest_nav['isin'] == FLEXICAP1_ISIN]) > 0 else 0
    flexicap1_corpus = float(flexicap1_nav) * flexicap1_units_sum
    flexicap2_nav = schemewise_latest_nav[schemewise_latest_nav['isin'] == FLEXICAP2_ISIN]['nav'].values[0] if len(schemewise_latest_nav[schemewise_latest_nav['isin'] == FLEXICAP2_ISIN]) > 0 else 0
    flexicap2_corpus = float(flexicap2_nav) * flexicap2_units_sum

    total_indexed_amount = 0.0
    for row in df_gdp_inflation_info_sorted.itertuples():
        total_indexed_amount += row.total_value * row.cagr

    print(Fore.GREEN + f"  {' ':40.40}| Total EQUITY Market Value              = {round(total_market_value,0)}")   
    print(Fore.GREEN + f"  {' ':40.40}| Total EQUITY Invested Amount           = {round(total_invested_amount,0)}")            
    print(Fore.GREEN + f"  {' ':40.40}| Total NIFTY 50 Indexed Amount          = {round(nifty50_corpus,0)}")   
    print(Fore.GREEN + f"  {' ':40.40}| Total FLEXI CAP 1 Indexed Amount       = {round(flexicap1_corpus,0)}")   
    print(Fore.GREEN + f"  {' ':40.40}| Total FLEXI CAP 2 Indexed Amount       = {round(flexicap2_corpus,0)}")   
    print(Fore.GREEN + f"  {' ':40.40}| Total GDP + Inflation Indexed Amount   = {round(total_indexed_amount,0)}")   

@log_function_call
def provide_insights(statements: list) -> None:

    if len(statements) > 1:
        print(Fore.GREEN + f"\r* {"PROCESSING MODE":40.40}| FAMILY PORTFOLIO\033[K", end="\n", flush=True)
    else:
        print(Fore.GREEN + f"\r* {"PROCESSING MODE":40.40}| INDIVIDUAL PORTFOLIO\033[K", end="\n", flush=True)

    schemewise_latest_nav = get_latest_nav_for_all_schemes()    

    dict_of_investor_statements = load_statement(statements)
    classify_funds(dict_of_investor_statements)
    check_active_funds(dict_of_investor_statements)
    check_data_deficiency(dict_of_investor_statements)
    compute_full_xirr(dict_of_investor_statements)
    compute_schemewise_xirr(dict_of_investor_statements)
    search_for_regular_plans(schemewise_latest_nav)
    check_optimum_funds()
    check_dividend_plan_funds(schemewise_latest_nav)
    check_fund_house_concentration(dict_of_investor_statements,schemewise_latest_nav)
    compare_with_index(dict_of_investor_statements, schemewise_latest_nav)
        

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
            "  --pdf PATH       Required PDF statement path for listing insights.\n"
            "  --password TEXT  Optional PDF password. If omitted, the program "
            "prompts without echoing the password.\n"
        )
        return 0

    if args.action == "version":
        print("Version = " + VERSION)
        return 0

    if args.action == "list":
        password = args.password
        if password is None:
            password = getpass.getpass("PDF password: ")
        try:
            statements = parse_statement(args.files, password)
            provide_insights(statements)
        except (FileNotFoundError, OSError, subprocess.CalledProcessError, ValueError, RuntimeError) as error:
            print(Fore.RED + f"ERROR: {error}", file=sys.stderr)
            return 1
        return 0

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
