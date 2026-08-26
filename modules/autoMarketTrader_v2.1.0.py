#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# Auto Market Trader v2.1.0

MODULE_NAME = "Auto Market Trader"
MODULE_ENTRY = "autoMarketTrader"

import csv
import json
import math
import os
import re
import shutil
import sys
import time
import traceback
from datetime import datetime
from decimal import Decimal

from ikabot import config
from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import *
from ikabot.helpers.market import *
from ikabot.helpers.naval import getAvailableShips
from ikabot.helpers.pedirInfo import read, getShipCapacity
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import addThousandSeparator, wait, getDateTime
from ikabot.function.sellResources import getMarketInfo
from ikabot.function.buyResources import getOffers, buy


# ============================================================
#  Constants
# ============================================================

MODULE_VERSION = "v2.1.0"

TRADE_BUY = "333"
TRADE_SELL = "444"
MAX_BUY_AMOUNT = 40000000

RESOURCE_PARAMS = [
    ("resource", "resourcePrice", "resourceTradeType"),
    ("tradegood1", "tradegood1Price", "tradegood1TradeType"),
    ("tradegood2", "tradegood2Price", "tradegood2TradeType"),
    ("tradegood3", "tradegood3Price", "tradegood3TradeType"),
    ("tradegood4", "tradegood4Price", "tradegood4TradeType"),
]

CSV_COLUMNS = [
    "order_id",
    "priority",
    "resource",
    "order_type",
    "mode",
    "strategy",
    "target_player",
    "price",
    "quantity_remaining",
    "quantity_fulfilled",
    "per_cycle",
    "city_id",
    "city_name",
    "undercutting",
    "recurring",
    "daily_budget",
    "daily_spent",
    "daily_reset_date",
    "status",
    "last_activity",
    "last_posted",
    "error_count",
    "notes",
]

PRICE_LOG_COLUMNS = [
    "timestamp",
    "resource",
    "lowest_sell",
    "highest_buy",
    "num_offers",
    "city_id",
]

MAX_ERROR_COUNT = 5

VALID_RESOURCES = ["Wood", "Wine", "Marble", "Crystal", "Sulfur"]
VALID_ORDER_TYPES = ["buy", "sell"]
VALID_MODES = ["own_offer", "active"]
VALID_STRATEGIES = ["cheapest", "closest", "specific", ""]
VALID_STATUSES = ["pending", "active", "complete", "paused", "error"]


# ============================================================
#  Market Parsing
# ============================================================

def getActionPoints(html):
    """Parse action points from a full city page. -1 when unparseable."""
    match = re.search(r'js_GlobalMenu_maxActionPoints"[^>]*>(\d+)<', html)
    return int(match.group(1)) if match else -1


def getPriceLimits(html):
    """Per-resource (min, max) price bounds from the own-offers page."""
    limits = [(int(lower), int(upper)) for upper, lower in
              re.findall(r"'upper':\s*(\d+),\s*'lower':\s*(\d+)", html)[:5]]
    while len(limits) < 5:
        limits.append((1, 999999))
    return limits


def _input_value(html, field_name):
    tag = re.search(r'<input[^>]*\bname="{}"[^>]*>'.format(re.escape(field_name)), html)
    if tag is None:
        return None
    value = re.search(r'\bvalue="(\d+)"', tag.group(0))
    return int(value.group(1)) if value else None


def getOwnOfferPrices(html):
    """Price of each own-offer slot. None where the page could not be parsed."""
    return [_input_value(html, price_key) for _, price_key, _ in RESOURCE_PARAMS]


def getOwnOfferTradeTypes(html):
    """Trade type of each own-offer slot, defaulting to sell."""
    types = []
    for _, _, type_key in RESOURCE_PARAMS:
        name = re.escape(type_key)
        found = (
            re.search(r'<input[^>]*\bname="{}"[^>]*\bvalue="(333|444)"[^>]*\bchecked'.format(name), html)
            or re.search(r'<input[^>]*\bchecked[^>]*\bname="{}"[^>]*\bvalue="(333|444)"'.format(name), html)
            or re.search(r'<select[^>]*\bname="{}"[^>]*>[\s\S]*?<option[^>]*\bvalue="(333|444)"[^>]*\bselected'.format(name), html)
        )
        types.append(found.group(1) if found else TRADE_SELL)
    return types


def _scan_market(session, city, resource_index, trade_type):
    """Best price other players offer for a resource, plus the offer count.
    TRADE_SELL scans their sell offers (lowest wins), TRADE_BUY their buy
    offers (highest wins). Best price is None when nobody is offering.
    """
    if str(trade_type) == TRADE_BUY:
        offers = _get_buy_offers(session, city, resource_index)
    else:
        _set_resource_filter(session, city, resource_index, TRADE_SELL)
        offers = getOffers(session, city)
    prices = [o["precio"] for o in offers if o.get("amountAvailable", 0) > 0]
    if not prices:
        return None, len(offers)
    best = max(prices) if str(trade_type) == TRADE_BUY else min(prices)
    return best, len(offers)


def scanMarketPrices(session, city, resource_index, trade_type):
    try:
        return _scan_market(session, city, resource_index, trade_type)[0]
    except Exception:
        return None


def filter_offers_by_player(offers, player_name):
    target = str(player_name).strip().lower()
    return [o for o in offers if str(o.get("jugadorAComprar", "")).strip().lower() == target]


def filter_offers_by_max_price(offers, max_price):
    return [o for o in offers if o.get("precio", 0) <= max_price]


def sort_offers_by_distance(offers):
    """Closest first — bienesXminuto is goods per minute, so higher is nearer."""
    return sorted(offers, key=lambda o: -o.get("bienesXminuto", 0))


# ============================================================
#  CSV Helpers
# ============================================================

def _data_path(session, template):
    server = "s{}-{}".format(session.mundo, session.servidor)
    return os.path.join(IKABOT_DATA_DIR, template.format(server))


def get_csv_path(session):
    """Order CSV, per account, under IKABOT_DATA_DIR."""
    return _data_path(session, "autotrader_{}.csv")


def get_price_log_path(session):
    return _data_path(session, "autotrader_prices_{}.csv")


def get_settings_path(session):
    return _data_path(session, "autotrader_{}_settings.json")


def save_settings(path, interval, daily_summary, price_tracking, city_id):
    """Persist scheduler settings to JSON."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = {
        "interval": interval,
        "daily_summary": daily_summary,
        "price_tracking": price_tracking,
        "last_city_id": str(city_id),
    }
    with open(path, "w") as f:
        json.dump(data, f)


def load_settings(path):
    """Load scheduler settings from JSON. Returns dict or None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if "interval" in data and "last_city_id" in data:
            return data
    except (json.JSONDecodeError, IOError):
        pass
    return None


def read_orders(csv_path):
    """Read orders from CSV file.
    Returns list of order dicts. Skips invalid rows gracefully.
    Returns empty list if file doesn't exist.
    """
    if not os.path.exists(csv_path):
        return []
    orders = []
    int_fields = [
        "order_id", "priority", "price", "quantity_remaining",
        "quantity_fulfilled", "per_cycle", "daily_budget", "daily_spent",
        "last_posted", "error_count",
    ]
    try:
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if validate_order(row):
                    for field in int_fields:
                        try:
                            row[field] = int(row[field]) if row.get(field) else 0
                        except (ValueError, KeyError):
                            row[field] = 0
                    if "daily_reset_date" not in row or not row["daily_reset_date"]:
                        row["daily_reset_date"] = ""
                    orders.append(row)
    except Exception:
        err_path = csv_path + ".read_error.log"
        try:
            with open(err_path, "a") as ef:
                ef.write("{} read_orders error: {}\n".format(
                    datetime.now().isoformat(), traceback.format_exc()))
        except IOError:
            pass
    return orders


def write_orders(csv_path, orders):
    """Write orders to CSV with backup and atomic replace."""
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    backup = csv_path + ".bak"
    if os.path.exists(csv_path):
        shutil.copy2(csv_path, backup)
    tmp = csv_path + ".tmp"
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for order in orders:
            writer.writerow(order)
    os.replace(tmp, csv_path)


def next_order_id(orders):
    """Get next available order ID."""
    if not orders:
        return 1
    return max(int(o.get("order_id", 0)) for o in orders) + 1


def validate_order(order):
    """Check that an order row has the minimum required fields with valid values.
    Returns True if usable, False if it should be skipped.
    """
    if not order.get("resource") or order["resource"] not in VALID_RESOURCES:
        return False
    if not order.get("order_type") or order["order_type"] not in VALID_ORDER_TYPES:
        return False
    if not order.get("mode") or order["mode"] not in VALID_MODES:
        return False
    if order.get("strategy", "") not in VALID_STRATEGIES:
        return False
    if not order.get("status") or order["status"] not in VALID_STATUSES:
        return False
    try:
        remaining = int(order.get("quantity_remaining", 0))
        if remaining < 0:
            return False
    except (ValueError, TypeError):
        return False
    try:
        price = int(order.get("price", 0))
        if price <= 0:
            return False
    except (ValueError, TypeError):
        return False
    return True


def verify_csv(csv_path, commercial_cities=None):
    """Deep verification of CSV contents. Catches mistakes a human editor could make.
    Returns (valid_orders, warnings) where warnings is a list of human-readable strings.
    If commercial_cities is provided, also validates city_id references.
    """
    warnings = []

    if not os.path.exists(csv_path):
        return [], ["CSV file not found: {}".format(csv_path)]

    valid_city_ids = set()
    city_name_map = {}
    if commercial_cities:
        for c in commercial_cities:
            cid = str(c["id"])
            valid_city_ids.add(cid)
            city_name_map[cid] = c["name"]

    valid_orders = []
    seen_ids = set()
    row_num = 0

    try:
        with open(csv_path, "r", newline="") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                return [], ["CSV file is empty or has no header row"]

            missing_cols = [c for c in CSV_COLUMNS if c not in reader.fieldnames]
            if missing_cols:
                warnings.append("Missing columns: {}. Old CSV format?".format(", ".join(missing_cols)))

            extra_cols = [c for c in reader.fieldnames if c not in CSV_COLUMNS]
            if extra_cols:
                warnings.append("Unexpected columns (will be ignored): {}".format(", ".join(extra_cols)))

            for row in reader:
                row_num += 1
                row_warnings = []
                oid_raw = row.get("order_id", "")
                label = "Row {}".format(row_num)

                # order_id
                try:
                    oid = int(oid_raw) if oid_raw else 0
                    if oid <= 0:
                        row_warnings.append("order_id must be a positive integer, got '{}'".format(oid_raw))
                    elif oid in seen_ids:
                        row_warnings.append("duplicate order_id {}".format(oid))
                    else:
                        seen_ids.add(oid)
                        label = "Order #{}".format(oid)
                except ValueError:
                    row_warnings.append("order_id '{}' is not a number".format(oid_raw))

                # resource
                res = row.get("resource", "").strip()
                if not res:
                    row_warnings.append("resource is empty")
                elif res not in VALID_RESOURCES:
                    close = [r for r in VALID_RESOURCES if r.lower() == res.lower()]
                    if close:
                        row_warnings.append("resource '{}' has wrong case, should be '{}'".format(res, close[0]))
                    else:
                        row_warnings.append("resource '{}' is not valid. Must be one of: {}".format(
                            res, ", ".join(VALID_RESOURCES)))

                # order_type
                ot = row.get("order_type", "").strip()
                if ot not in VALID_ORDER_TYPES:
                    row_warnings.append("order_type '{}' invalid. Must be 'buy' or 'sell'".format(ot))

                # mode
                mode = row.get("mode", "").strip()
                if mode not in VALID_MODES:
                    row_warnings.append("mode '{}' invalid. Must be 'own_offer' or 'active'".format(mode))

                # strategy
                strat = row.get("strategy", "").strip()
                if strat and strat not in VALID_STRATEGIES:
                    row_warnings.append("strategy '{}' invalid. Must be one of: cheapest, closest, specific".format(strat))
                if mode == "active" and not strat:
                    row_warnings.append("active mode requires a strategy (cheapest/closest/specific)")
                if strat == "specific" and not row.get("target_player", "").strip():
                    row_warnings.append("strategy is 'specific' but target_player is empty")
                if mode == "own_offer" and strat:
                    row_warnings.append("own_offer mode does not use strategy (should be blank)")

                # status
                status = row.get("status", "").strip()
                if status not in VALID_STATUSES:
                    row_warnings.append("status '{}' invalid. Must be one of: {}".format(
                        status, ", ".join(VALID_STATUSES)))

                # Numeric fields
                int_checks = {
                    "price": (1, 999999, True),
                    "quantity_remaining": (0, 999999999, True),
                    "quantity_fulfilled": (0, 999999999, False),
                    "per_cycle": (0, 999999999, False),
                    "priority": (1, 3, True),
                    "daily_budget": (0, 999999999, False),
                    "daily_spent": (0, 999999999, False),
                    "error_count": (0, 999, False),
                    "last_posted": (0, 999999999, False),
                }
                for field, (lo, hi, required) in int_checks.items():
                    val_raw = row.get(field, "").strip()
                    if not val_raw:
                        if required:
                            row_warnings.append("{} is empty (required)".format(field))
                        continue
                    try:
                        val = int(val_raw)
                        if val < lo or val > hi:
                            row_warnings.append("{} value {} is out of range [{}, {}]".format(field, val, lo, hi))
                    except ValueError:
                        row_warnings.append("{} '{}' is not a valid number".format(field, val_raw))

                # city_id
                cid = row.get("city_id", "").strip()
                if not cid:
                    row_warnings.append("city_id is empty")
                elif valid_city_ids and cid not in valid_city_ids:
                    row_warnings.append("city_id '{}' does not match any Trading Post city. Valid: {}".format(
                        cid, ", ".join("{} ({})".format(k, city_name_map.get(k, "?")) for k in sorted(valid_city_ids))))

                # city_name mismatches
                cname = row.get("city_name", "").strip()
                if cid and cid in city_name_map and cname and cname != city_name_map[cid]:
                    row_warnings.append("city_name '{}' doesn't match city_id {} (expected '{}')".format(
                        cname, cid, city_name_map[cid]))

                # undercutting
                uc = row.get("undercutting", "").strip()
                if uc and uc not in ("yes", "no"):
                    row_warnings.append("undercutting '{}' invalid. Must be 'yes' or 'no'".format(uc))
                if uc == "yes" and mode == "active":
                    row_warnings.append("undercutting only applies to own_offer mode")

                # recurring
                rec = row.get("recurring", "").strip()
                if rec and rec != "none":
                    if not (rec.startswith("amount:") or rec.startswith("budget:")):
                        row_warnings.append("recurring '{}' invalid. Must be 'none', 'amount:N', or 'budget:N'".format(rec))
                    else:
                        parts = rec.split(":", 1)
                        try:
                            rval = int(parts[1])
                            if rval <= 0:
                                row_warnings.append("recurring value must be positive, got {}".format(rval))
                        except (ValueError, IndexError):
                            row_warnings.append("recurring '{}' has invalid number after ':'".format(rec))

                # Logic checks
                try:
                    qty_remaining = int(row.get("quantity_remaining", 0) or 0)
                    if status == "active" and qty_remaining <= 0:
                        row_warnings.append("status is 'active' but quantity_remaining is 0 (should be 'complete')")
                    if status == "complete" and qty_remaining > 0:
                        row_warnings.append("status is 'complete' but quantity_remaining is {} (should be 'active')".format(qty_remaining))
                    daily_budget = int(row.get("daily_budget", 0) or 0)
                    daily_spent = int(row.get("daily_spent", 0) or 0)
                    if daily_budget > 0 and daily_spent > daily_budget:
                        row_warnings.append("daily_spent ({}) exceeds daily_budget ({})".format(daily_spent, daily_budget))
                except (ValueError, TypeError):
                    pass

                if row_warnings:
                    for w in row_warnings:
                        warnings.append("{}: {}".format(label, w))
                else:
                    if validate_order(row):
                        valid_orders.append(row)

    except Exception:
        warnings.append("Failed to read CSV: {}".format(traceback.format_exc()[-200:]))

    return valid_orders, warnings


def make_order(order_id, resource, order_type, mode, price, quantity,
               city_id, city_name, priority=2, strategy="", target_player="",
               per_cycle=0, undercutting="no", recurring="none",
               daily_budget=0):
    """Create a new order dict with all fields populated."""
    return {
        "order_id": order_id,
        "priority": priority,
        "resource": resource,
        "order_type": order_type,
        "mode": mode,
        "strategy": strategy,
        "target_player": target_player,
        "price": price,
        "quantity_remaining": quantity,
        "quantity_fulfilled": 0,
        "per_cycle": per_cycle,
        "city_id": city_id,
        "city_name": city_name,
        "undercutting": undercutting,
        "recurring": recurring,
        "daily_budget": daily_budget,
        "daily_spent": 0,
        "daily_reset_date": "",
        "status": "pending",
        "last_activity": getDateTime(),
        "last_posted": 0,
        "error_count": 0,
        "notes": "",
    }


def log_price(price_log_path, resource, lowest_sell, highest_buy, num_offers, city_id):
    """Append a price observation to the price log CSV."""
    os.makedirs(os.path.dirname(price_log_path) or ".", exist_ok=True)
    file_exists = os.path.exists(price_log_path)
    with open(price_log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PRICE_LOG_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "timestamp": getDateTime(),
            "resource": resource,
            "lowest_sell": lowest_sell if lowest_sell is not None else "",
            "highest_buy": highest_buy if highest_buy is not None else "",
            "num_offers": num_offers,
            "city_id": city_id,
        })


def display_orders(orders):
    """Print a formatted table of orders."""
    if not orders:
        print("No orders found.")
        return
    print("{:<4} {:<3} {:<8} {:<4} {:<10} {:<8} {:<12} {:<12} {:<12} {:<7}".format(
        "ID", "Pri", "Resource", "Type", "Mode", "Strategy", "Remaining", "Fulfilled", "Price", "Status",
    ))
    print("-" * 95)
    for o in orders:
        print("{:<4} {:<3} {:<8} {:<4} {:<10} {:<8} {:<12} {:<12} {:<12} {:<7}".format(
            o["order_id"],
            o["priority"],
            str(o["resource"])[:7],
            str(o["order_type"])[:4],
            str(o["mode"])[:10],
            str(o.get("strategy", ""))[:8],
            addThousandSeparator(o["quantity_remaining"]),
            addThousandSeparator(o["quantity_fulfilled"]),
            addThousandSeparator(o["price"]),
            str(o["status"])[:7],
        ))
    print("")


def _refresh_city(session, city):
    """Re-fetch city data to get current resource levels."""
    html = session.get(city_url + city["id"])
    fresh = getCity(html)
    fresh["pos"] = city["pos"]
    fresh["rango"] = city["rango"]
    return fresh


# ============================================================
#  Setup Flow
# ============================================================

def print_module_banner(page_title=None):
    banner()
    print("╔═══════════════════════════════════════════════════════════╗")
    print("║                AUTO MARKET TRADER  {}                 ║".format(MODULE_VERSION))
    print("╚═══════════════════════════════════════════════════════════╝")
    if page_title:
        print("\n{}".format(page_title))
        print("──────────────────────────────────────────────────────────")
    print("")


def _choose_city(commercial_cities):
    """City picker for auto trader."""
    print("Which city's Trading Post should manage orders?\n")
    for i, city in enumerate(commercial_cities):
        print("({:d}) {}".format(i + 1, city["name"]))
    ind = read(min=1, max=len(commercial_cities))
    return commercial_cities[ind - 1]


def _show_city_info(session, city):
    """Display city resources, gold, and action points."""
    gold, gold_prod = getGold(session, city)
    html = session.get(city_url + city["id"])
    ap = getActionPoints(html)

    print("City: {}".format(city["name"]))
    print("Gold: {} (production: {}/hr)".format(
        addThousandSeparator(gold), addThousandSeparator(gold_prod)))
    if ap >= 0:
        print("Action Points: {}".format(ap))
    print("")
    print("Resources:")
    for i in range(5):
        avail = city["availableResources"][i] if i < len(city.get("availableResources", [])) else 0
        free = city["freeSpaceForResources"][i] if i < len(city.get("freeSpaceForResources", [])) else 0
        print("  {}: {} available, {} free space".format(
            materials_names[i],
            addThousandSeparator(avail),
            addThousandSeparator(free),
        ))
    print("")


def _create_order(session, city, orders, commercial_cities):
    """Interactive order creation. Returns a new order dict or None if cancelled."""
    oid = next_order_id(orders)

    # If multiple cities, let user pick which one for this order
    if len(commercial_cities) > 1:
        print("Which Trading Post for this order?")
        for i, c in enumerate(commercial_cities):
            marker = " (current)" if c["id"] == city["id"] else ""
            print("({:d}) {}{}".format(i + 1, c["name"], marker))
        idx = read(min=1, max=len(commercial_cities))
        order_city = commercial_cities[idx - 1]
    else:
        order_city = city

    # Resource
    print("\nResource:")
    for i, name in enumerate(materials_names):
        print("({:d}) {}".format(i + 1, name))
    res_idx = read(min=1, max=5) - 1
    resource = materials_names[res_idx]

    # Buy or Sell
    print("\n(1) Buy {}  (2) Sell {}".format(resource, resource))
    order_type = "buy" if read(min=1, max=2) == 1 else "sell"

    # Mode
    print("\nTrading mode:")
    print("(1) Own offer - post on marketplace, wait for others")
    print("(2) Active trading - browse offers, buy/sell and ship")
    mode = "own_offer" if read(min=1, max=2) == 1 else "active"

    # Strategy (active mode only)
    strategy = ""
    target_player = ""
    if mode == "active":
        print("\nBuy strategy:" if order_type == "buy" else "\nSell strategy:")
        print("(1) Cheapest price")
        print("(2) Closest distance")
        print("(3) Specific player")
        strat_choice = read(min=1, max=3)
        strategy = ["cheapest", "closest", "specific"][strat_choice - 1]
        if strategy == "specific":
            target_player = read(msg="Player name: ")
            if not target_player.strip():
                print("No player name entered, cancelled.")
                return None

    # Price
    if mode == "own_offer":
        html = getMarketInfo(session, order_city)
        limits = getPriceLimits(html)
        lo, hi = limits[res_idx]
        print("\nPrice per unit [min={}, max={}]:".format(lo, hi))
        price = read(min=lo, max=hi)
    else:
        if order_type == "buy":
            print("\nMax price willing to pay per unit:")
        else:
            print("\nMin price willing to accept per unit:")
        price = read(min=1, max=999999)

    # Quantity
    print("\nTotal quantity to {}:".format(order_type))
    quantity = read(min=1, max=999999999)

    # Per-cycle limit
    print("\nMax amount per cycle (0 = no limit) [default 0]:")
    per_cycle_input = read(min=0, max=quantity, empty=True)
    per_cycle = int(per_cycle_input) if per_cycle_input != "" else 0

    # Priority
    print("\nPriority:")
    print("(1) High  (2) Medium  (3) Low")
    priority = read(min=1, max=3)

    # Undercutting (own_offer mode)
    undercutting = "no"
    if mode == "own_offer":
        print("\nAuto-undercut/outbid each cycle? [y/N]")
        uc = read(values=["y", "Y", "n", "N", ""])
        if uc.lower() == "y":
            undercutting = "yes"

    # Recurring
    recurring = "none"
    daily_budget = 0
    print("\nWhen order completes:")
    print("(0) Stop - mark as complete")
    print("(1) Re-order same amount")
    print("(2) Re-order with daily gold budget")
    rec_choice = read(min=0, max=2)
    if rec_choice == 1:
        recurring = "amount:{}".format(quantity)
    elif rec_choice == 2:
        print("Daily gold budget:")
        daily_budget = read(min=1, max=999999999)
        recurring = "budget:{}".format(daily_budget)

    order = make_order(
        order_id=oid,
        resource=resource,
        order_type=order_type,
        mode=mode,
        price=price,
        quantity=quantity,
        city_id=str(order_city["id"]),
        city_name=order_city["name"],
        priority=priority,
        strategy=strategy,
        target_player=target_player.strip(),
        per_cycle=per_cycle,
        undercutting=undercutting,
        recurring=recurring,
        daily_budget=daily_budget,
    )

    # Confirm
    print("\n--- Order Summary ---")
    action = "BUY" if order_type == "buy" else "SELL"
    print("  {} {} {} @ {} per unit".format(
        action, addThousandSeparator(quantity), resource, price))
    print("  Mode: {} | Strategy: {}".format(mode, strategy or "n/a"))
    if target_player:
        print("  Target player: {}".format(target_player))
    print("  Priority: {} | Per-cycle: {} | City: {}".format(
        priority, addThousandSeparator(per_cycle) if per_cycle else "unlimited", order_city["name"]))
    if recurring != "none":
        print("  Recurring: {}".format(recurring))
    print("\nAdd this order? [Y/n]")
    rta = read(values=["y", "Y", "n", "N", ""])
    if rta.lower() == "n":
        return None

    return order


def autoMarketTrader(session, event, stdin_fd, predetermined_input):
    """Entry point for the auto market trader."""
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    try:
        print_module_banner()

        commercial_cities = getCommercialCities(session)
        if not commercial_cities:
            print("No city has a Trading Post built.")
            enter()
            event.set()
            return

        csv_path = get_csv_path(session)
        settings_path = get_settings_path(session)
        existing_orders = read_orders(csv_path)
        saved_settings = load_settings(settings_path)

        # Quick-start: CSV exists with active orders AND saved settings exist
        active_existing = [o for o in existing_orders if o["status"] in ("pending", "active")]
        if active_existing and saved_settings:
            city_lookup = {str(c["id"]): c for c in commercial_cities}
            saved_city = city_lookup.get(str(saved_settings.get("last_city_id", "")))

            if saved_city:
                # Verify CSV integrity
                _, csv_warnings = verify_csv(csv_path, commercial_cities)
                if csv_warnings:
                    print("CSV verification warnings:\n")
                    for w in csv_warnings:
                        print("  ! {}".format(w))
                    print("")

                last_activity = ""
                for o in existing_orders:
                    la = str(o.get("last_activity", ""))
                    if la > last_activity:
                        last_activity = la

                print("{} active orders found{}".format(
                    len(active_existing),
                    " (last run: {})".format(last_activity) if last_activity else ""))
                print("City: {} | Interval: {} min | Summary: {} | Prices: {}".format(
                    saved_city["name"],
                    saved_settings.get("interval", 60),
                    "ON" if saved_settings.get("daily_summary") else "OFF",
                    "ON" if saved_settings.get("price_tracking") else "OFF"))
                print("")
                display_orders(existing_orders)

                print("(s) Start trading with these settings")
                print("(1) Add new orders alongside existing")
                print("(2) Clear all orders and start fresh")
                print("(3) Full setup (change settings)")
                choice = read(values=["s", "S", "1", "2", "3"])

                if choice in ("s", "S"):
                    city = _refresh_city(session, saved_city)
                    _launch_background(
                        session, event, csv_path, city, existing_orders,
                        saved_settings.get("interval", 60),
                        saved_settings.get("daily_summary", False),
                        saved_settings.get("price_tracking", False))
                    return
                elif choice == "1":
                    if len(commercial_cities) == 1:
                        city = commercial_cities[0]
                    else:
                        city = _choose_city(commercial_cities)
                    city = _refresh_city(session, city)
                    print_module_banner("City Overview")
                elif choice == "2":
                    existing_orders = []
                    if len(commercial_cities) == 1:
                        city = commercial_cities[0]
                    else:
                        city = _choose_city(commercial_cities)
                    city = _refresh_city(session, city)
                    print("Orders cleared.\n")
                    print_module_banner("City Overview")
                elif choice == "3":
                    if len(commercial_cities) == 1:
                        city = commercial_cities[0]
                    else:
                        city = _choose_city(commercial_cities)
                    city = _refresh_city(session, city)
                    print_module_banner("Launch Settings")
                    print("Launching with existing {} orders.\n".format(len(active_existing)))
                    display_orders(existing_orders)

                    print("Check interval in minutes [15-240, default 60]:")
                    interval_input = read(min=15, max=240, empty=True)
                    interval = int(interval_input) if interval_input != "" else 60

                    print("\nDaily summary notifications? [y/N]")
                    ds = read(values=["y", "Y", "n", "N", ""])
                    daily_summary = ds.lower() == "y"

                    print("Price tracking? [y/N]")
                    pt = read(values=["y", "Y", "n", "N", ""])
                    price_tracking = pt.lower() == "y"

                    print("\nProceed? [Y/n]")
                    rta = read(values=["y", "Y", "n", "N", ""])
                    if rta.lower() == "n":
                        event.set()
                        return

                    save_settings(settings_path, interval, daily_summary, price_tracking, city["id"])
                    _launch_background(session, event, csv_path, city, existing_orders,
                                       interval, daily_summary, price_tracking)
                    return
            else:
                # Saved city no longer exists, fall through to normal flow
                if len(commercial_cities) == 1:
                    city = commercial_cities[0]
                else:
                    city = _choose_city(commercial_cities)
                city = _refresh_city(session, city)

        elif existing_orders:
            # Have orders but no saved settings — show old menu without quick-start
            if len(commercial_cities) == 1:
                city = commercial_cities[0]
            else:
                city = _choose_city(commercial_cities)
            city = _refresh_city(session, city)

            _, csv_warnings = verify_csv(csv_path, commercial_cities)
            if csv_warnings:
                print("CSV verification warnings:\n")
                for w in csv_warnings:
                    print("  ! {}".format(w))
                print("")

            print("Existing orders found:\n")
            display_orders(existing_orders)
            print("What would you like to do?")
            print("(1) Add new orders alongside existing")
            print("(2) Clear all orders and start fresh")
            print("(3) Run with current orders (skip setup)")
            choice = read(min=1, max=3)

            if choice == 2:
                existing_orders = []
                print("Orders cleared.\n")
            elif choice == 3:
                print_module_banner("Launch Settings")
                print("Launching with existing {} orders.\n".format(len(existing_orders)))
                display_orders(existing_orders)

                print("Check interval in minutes [15-240, default 60]:")
                interval_input = read(min=15, max=240, empty=True)
                interval = int(interval_input) if interval_input != "" else 60

                print("\nDaily summary notifications? [y/N]")
                ds = read(values=["y", "Y", "n", "N", ""])
                daily_summary = ds.lower() == "y"

                print("Price tracking? [y/N]")
                pt = read(values=["y", "Y", "n", "N", ""])
                price_tracking = pt.lower() == "y"

                print("\nProceed? [Y/n]")
                rta = read(values=["y", "Y", "n", "N", ""])
                if rta.lower() == "n":
                    event.set()
                    return

                save_settings(settings_path, interval, daily_summary, price_tracking, city["id"])
                _launch_background(session, event, csv_path, city, existing_orders,
                                   interval, daily_summary, price_tracking)
                return
            print_module_banner("City Overview")

        else:
            # No existing orders — fresh setup
            if len(commercial_cities) == 1:
                city = commercial_cities[0]
            else:
                city = _choose_city(commercial_cities)
            print_module_banner("City: {}".format(city["name"]))
            city = _refresh_city(session, city)

        # Show city info
        _show_city_info(session, city)

        html = getMarketInfo(session, city)
        storage_cap = storageCapacityOfMarket(html)
        print("Trading Post storage capacity: {}\n".format(addThousandSeparator(storage_cap)))

        # Order creation loop
        orders = list(existing_orders)
        while True:
            print("--- Create Order #{} ---".format(next_order_id(orders)))
            new_order = _create_order(session, city, orders, commercial_cities)
            if new_order:
                orders.append(new_order)
                print("\nOrder #{} added.\n".format(new_order["order_id"]))

            if not any(o["status"] in ("pending", "active") for o in orders):
                print("No active orders yet. Add at least one order.")
                continue

            print("Add another order? [y/N]")
            more = read(values=["y", "Y", "n", "N", ""])
            if more.lower() != "y":
                break
            print_module_banner("Create Order")

        # Settings
        print_module_banner("Settings")

        print("Check interval in minutes [15-240, default 60]:")
        interval_input = read(min=15, max=240, empty=True)
        interval = int(interval_input) if interval_input != "" else 60

        print("\nDaily summary notifications? [y/N]")
        ds = read(values=["y", "Y", "n", "N", ""])
        daily_summary = ds.lower() == "y"

        print("Price tracking (log prices each cycle)? [y/N]")
        pt = read(values=["y", "Y", "n", "N", ""])
        price_tracking = pt.lower() == "y"

        # Final summary
        print_module_banner("Summary")
        print("City: {} | Interval: {} min".format(city["name"], interval))
        print("Daily summary: {} | Price tracking: {}".format(
            "ON" if daily_summary else "OFF",
            "ON" if price_tracking else "OFF"))
        print("")
        display_orders(orders)

        print("Proceed? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            event.set()
            return

        # Write CSV, save settings, and launch
        write_orders(csv_path, orders)
        save_settings(settings_path, interval, daily_summary, price_tracking, city["id"])

        _launch_background(session, event, csv_path, city, orders,
                           interval, daily_summary, price_tracking)

    except KeyboardInterrupt:
        event.set()
        return
    except Exception:
        if event.is_set():
            raise
        print("\n{}Auto Market Trader setup failed:{}\n{}".format(
            bcolors.RED, bcolors.ENDC, traceback.format_exc()))
        enter()
        event.set()
        return


def _launch_background(session, event, csv_path, city, orders, interval,
                       daily_summary, price_tracking):
    """Fork background process and start the trading loop."""
    set_child_mode(session)
    event.set()

    info = "Auto Market Trader\n"
    info += "City: {} | Interval: {} min\n".format(city["name"], interval)
    info += "Orders: {}\n".format(len([o for o in orders if o["status"] in ("pending", "active")]))
    for o in orders:
        if o["status"] in ("pending", "active"):
            action = "BUY" if o["order_type"] == "buy" else "SELL"
            info += "  #{} {} {} {} @ {}\n".format(
                o["order_id"], action, addThousandSeparator(o["quantity_remaining"]),
                o["resource"], o["price"])
    setInfoSignal(session, info)

    settings = {
        "daily_summary": daily_summary,
        "price_tracking": price_tracking,
    }

    try:
        run_auto_trader(session, csv_path, interval, settings)
    except Exception:
        msg = "Error in Auto Market Trader:\n{}\nCause:\n{}".format(info, traceback.format_exc())
        sendToBot(session, msg)
    finally:
        session.logout()


# ============================================================
#  Background Loop + Own Offer Processing
# ============================================================

def _resource_index(resource_name):
    """Convert resource name to index (0-4)."""
    try:
        return VALID_RESOURCES.index(resource_name)
    except ValueError:
        return -1


def _build_own_offer_payload(city, resource_configs, existing_amounts, existing_prices,
                             existing_types, price_limits):
    """Build updateOffers payload. Preserves non-managed resources.
    resource_configs: dict mapping resource_index -> {type, amount, price}
    """
    payload = {
        "cityId": city["id"],
        "position": city["pos"],
        "action": "CityScreen",
        "function": "updateOffers",
        "backgroundView": "city",
        "currentCityId": city["id"],
        "templateView": "branchOfficeOwnOffers",
        "currentTab": "tab_branchOfficeOwnOffers",
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    for i, (amt_key, price_key, type_key) in enumerate(RESOURCE_PARAMS):
        if i in resource_configs:
            cfg = resource_configs[i]
            payload[type_key] = cfg["type"]
            payload[amt_key] = str(cfg["amount"])
            payload[price_key] = str(cfg["price"])
        else:
            slot_type = existing_types[i] if i < len(existing_types) else TRADE_SELL
            price = existing_prices[i] if i < len(existing_prices) else None
            if price is None:
                lo, hi = price_limits[i]
                price = lo if slot_type == TRADE_BUY else hi
            payload[type_key] = slot_type
            payload[amt_key] = str(existing_amounts[i])
            payload[price_key] = str(price)
    return payload


def process_own_offers(session, city, orders, gold):
    """Process all own_offer orders for a city.
    Detects fulfilled trades, updates CSV orders, posts new offers.
    Returns (updated_orders, notifications, remaining_gold).
    """
    notifications = []

    html = getMarketInfo(session, city)
    current_amounts = onSellInMarket(html)
    current_prices = getOwnOfferPrices(html)
    current_types = getOwnOfferTradeTypes(html)
    storage_cap = storageCapacityOfMarket(html)
    price_limits = getPriceLimits(html)

    city = _refresh_city(session, city)

    orders_by_resource = {}
    for o in orders:
        if o["mode"] != "own_offer" or o["status"] not in ("pending", "active"):
            continue
        idx = _resource_index(o["resource"])
        if idx < 0:
            continue
        if idx not in orders_by_resource:
            orders_by_resource[idx] = []
        orders_by_resource[idx].append(o)

    for idx in orders_by_resource:
        orders_by_resource[idx].sort(key=lambda o: (o["priority"], o["order_id"]))

    resource_configs = {}

    for idx, res_orders in orders_by_resource.items():
        if not res_orders:
            continue

        # One marketplace slot per resource: the highest-priority order owns it.
        # The rest must forget what they posted or they will read the owner's
        # trades as their own the next time they take the slot over.
        active_order = res_orders[0]
        active_order["status"] = "active"
        for other in res_orders[1:]:
            _set_last_posted(other, 0)

        # Detect fulfillment: if current amount is less than what we posted last time
        last_posted = _get_last_posted(active_order)
        current = current_amounts[idx]
        if last_posted > 0 and current < last_posted:
            fulfilled = last_posted - current
            active_order["quantity_fulfilled"] += fulfilled
            active_order["quantity_remaining"] = max(0, active_order["quantity_remaining"] - fulfilled)
            active_order["last_activity"] = getDateTime()

            if active_order["order_type"] == "sell":
                gold_earned = fulfilled * active_order["price"]
                notifications.append("#{} Sold {} {} @ {} = {} gold | {} remaining".format(
                    active_order["order_id"],
                    addThousandSeparator(fulfilled), active_order["resource"],
                    active_order["price"], addThousandSeparator(gold_earned),
                    addThousandSeparator(active_order["quantity_remaining"])))
            else:
                gold_spent = fulfilled * active_order["price"]
                notifications.append("#{} Bought {} {} @ {} = {} gold | {} remaining".format(
                    active_order["order_id"],
                    addThousandSeparator(fulfilled), active_order["resource"],
                    active_order["price"], addThousandSeparator(gold_spent),
                    addThousandSeparator(active_order["quantity_remaining"])))

            if active_order["quantity_remaining"] <= 0:
                active_order["status"] = "complete"
                notifications.append("ORDER #{} COMPLETE: {} {} total".format(
                    active_order["order_id"], active_order["order_type"].upper(),
                    addThousandSeparator(active_order["quantity_fulfilled"])))

        # A finished order hands the slot to the next one waiting on this
        # resource. With nobody waiting the slot is emptied, otherwise the
        # completed offer keeps trading untracked.
        while active_order["quantity_remaining"] <= 0:
            active_order["status"] = "complete"
            _set_last_posted(active_order, 0)
            res_orders = res_orders[1:]
            if not res_orders:
                active_order = None
                break
            active_order = res_orders[0]
            active_order["status"] = "active"

        if active_order is None:
            existing_type = current_types[idx] if idx < len(current_types) else TRADE_SELL
            existing_price = current_prices[idx] if idx < len(current_prices) else None
            if existing_price is None:
                lo, hi = price_limits[idx]
                existing_price = lo if existing_type == TRADE_BUY else hi
            resource_configs[idx] = {
                "type": existing_type, "amount": 0, "price": existing_price}
            continue

        # Handle undercutting
        price = active_order["price"]
        if active_order.get("undercutting") == "yes" and idx < len(price_limits):
            lo, hi = price_limits[idx]
            if active_order["order_type"] == "sell":
                lowest = scanMarketPrices(session, city, idx, TRADE_SELL)
                if lowest is not None and lowest > lo:
                    price = max(lo, lowest - 1)
            else:
                highest = scanMarketPrices(session, city, idx, TRADE_BUY)
                if highest is not None and highest < hi:
                    price = min(hi, highest + 1)
            active_order["price"] = price

        # Calculate amount to post
        desired = active_order["quantity_remaining"]
        if active_order["per_cycle"] > 0:
            desired = min(desired, active_order["per_cycle"])

        if active_order["order_type"] == "sell":
            # Limit by city available resources
            city_avail = city["availableResources"][idx] if idx < len(city.get("availableResources", [])) else 0
            desired = min(desired, city_avail)
            # Only goods on sale occupy trading-post storage; buy offers do not.
            other_storage = sum(
                current_amounts[j] for j in range(5)
                if j != idx and j not in resource_configs
                and (current_types[j] if j < len(current_types) else TRADE_SELL) == TRADE_SELL)
            managed_storage = sum(cfg["amount"] for j, cfg in resource_configs.items()
                                  if cfg["type"] == TRADE_SELL)
            available_storage = max(0, storage_cap - other_storage - managed_storage)
            desired = min(desired, available_storage)
            trade_type = TRADE_SELL
        else:
            # Buy order: limit by gold and max buy
            desired = min(desired, MAX_BUY_AMOUNT)
            if price > 0:
                desired = min(desired, gold // price)
            # Limit by warehouse space
            free_space = city["freeSpaceForResources"][idx] if idx < len(city.get("freeSpaceForResources", [])) else 0
            desired = min(desired, free_space)
            trade_type = TRADE_BUY

        amount = max(0, desired)
        # Reserve gold only for what is actually posted, after every cap.
        if trade_type == TRADE_BUY and price > 0:
            gold -= amount * price
        resource_configs[idx] = {"type": trade_type, "amount": amount, "price": price}
        _set_last_posted(active_order, amount)

    if resource_configs:
        payload = _build_own_offer_payload(city, resource_configs, current_amounts,
                                           current_prices, current_types, price_limits)
        session.post(params=payload)

    return orders, notifications, gold


def _get_last_posted(order):
    """Get last posted amount from dedicated column."""
    return int(order.get("last_posted", 0) or 0)


def _set_last_posted(order, amount):
    """Store last posted amount in dedicated column."""
    order["last_posted"] = amount


def _update_status(session, orders):
    """Update process status line with order summary."""
    active = [o for o in orders if o["status"] in ("pending", "active")]
    if not active:
        session.setStatus("AutoTrader: idle | {}".format(getDateTime()))
        return
    parts = []
    for o in active:
        action = "B" if o["order_type"] == "buy" else "S"
        res = o["resource"][:3]
        remaining = addThousandSeparator(o["quantity_remaining"])
        parts.append("#{}{}{} {}".format(o["order_id"], action, res, remaining))
    status = "AutoTrader: {} | {}".format(" ".join(parts[:5]), getDateTime())
    session.setStatus(status)


# ============================================================
#  Recurring Orders, Daily Summary, Price Tracking
# ============================================================

def handle_recurring(orders):
    """Handle completed orders that have recurring set.
    Resets quantity_remaining and status for recurring orders.
    """
    for o in orders:
        if o["status"] != "complete":
            continue
        recurring = str(o.get("recurring", "none"))
        if recurring == "none" or not recurring:
            continue

        if recurring.startswith("amount:"):
            try:
                amount = int(recurring.split(":")[1])
                o["quantity_remaining"] = amount
                o["quantity_fulfilled"] = 0
                o["error_count"] = 0
                o["status"] = "active"
                o["last_activity"] = getDateTime()
                o["notes"] = str(o.get("notes", "")) + ";reordered:{}".format(getDateTime())
            except (ValueError, IndexError):
                pass

        elif recurring.startswith("budget:"):
            try:
                budget = int(recurring.split(":")[1])
                price = int(o.get("price", 1))
                if price > 0:
                    quantity = budget // price
                    if quantity > 0:
                        o["quantity_remaining"] = quantity
                        o["quantity_fulfilled"] = 0
                        o["error_count"] = 0
                        o["daily_budget"] = budget
                        o["daily_spent"] = 0
                        o["status"] = "active"
                        o["last_activity"] = getDateTime()
                        o["notes"] = str(o.get("notes", "")) + ";reordered_budget:{}".format(getDateTime())
            except (ValueError, IndexError):
                pass


def _reset_daily_if_new_day(orders):
    """Reset daily_spent for all orders if a new day has started.
    Uses daily_reset_date to track when the last reset occurred per order,
    ensuring we reset exactly once per day regardless of activity.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    for o in orders:
        last_reset = str(o.get("daily_reset_date", ""))
        if last_reset != today:
            o["daily_spent"] = 0
            o["daily_reset_date"] = today


SUMMARY_FILE_SUFFIX = "_last_summary.txt"


def _maybe_send_daily_summary(session, orders, csv_path):
    """Send daily summary if 24 hours have passed since last one.
    Suppresses if nothing happened.
    """
    summary_file = csv_path + SUMMARY_FILE_SUFFIX
    now = time.time()

    # Check if 24h since last summary
    if os.path.exists(summary_file):
        try:
            with open(summary_file, "r") as f:
                last_ts = float(f.read().strip())
            if now - last_ts < 86400:
                return
        except (ValueError, IOError):
            pass

    # Check if anything happened today: daily_spent > 0 means gold was used,
    # or last_activity is from today with a status change (completed orders)
    today = datetime.now().strftime("%Y-%m-%d")
    activity_today = False
    for o in orders:
        if int(o.get("daily_spent", 0)) > 0:
            activity_today = True
            break
        last = str(o.get("last_activity", ""))
        if last.startswith(today) and o["status"] == "complete":
            activity_today = True
            break

    if not activity_today:
        return

    # Build summary
    completed = [o for o in orders if o["status"] == "complete"]
    active = [o for o in orders if o["status"] == "active"]
    total_fulfilled = {}
    total_spent = 0
    for o in orders:
        res = o["resource"]
        if res not in total_fulfilled:
            total_fulfilled[res] = 0
        total_fulfilled[res] += int(o.get("quantity_fulfilled", 0))
        total_spent += int(o.get("daily_spent", 0))

    msg = "Auto Trader Daily Summary:\n"
    msg += "  Active: {} | Completed: {}\n".format(len(active), len(completed))
    if total_fulfilled:
        msg += "  Traded: "
        parts = []
        for res, qty in total_fulfilled.items():
            if qty > 0:
                parts.append("{} {}".format(addThousandSeparator(qty), res))
        msg += ", ".join(parts) if parts else "nothing"
        msg += "\n"
    if total_spent > 0:
        msg += "  Gold spent today: {}\n".format(addThousandSeparator(total_spent))
    for o in active:
        pct = 0
        total = o["quantity_fulfilled"] + o["quantity_remaining"]
        if total > 0:
            pct = int(o["quantity_fulfilled"] * 100 / total)
        msg += "  #{} {} {} {}% done\n".format(
            o["order_id"], o["order_type"].upper(), o["resource"], pct)

    sendToBot(session, msg)

    # Record summary timestamp
    try:
        with open(summary_file, "w") as f:
            f.write(str(now))
    except IOError:
        pass


def _trim_notes(order, max_len=500):
    """Trim notes field to prevent unbounded growth."""
    notes = str(order.get("notes", ""))
    if len(notes) > max_len:
        order["notes"] = "..." + notes[-(max_len - 3):]


def _increment_error(order, reason=""):
    """Increment error count and escalate to error status if threshold reached."""
    order["error_count"] = int(order.get("error_count", 0)) + 1
    if reason:
        order["notes"] = str(order.get("notes", "")) + ";error:{}:{}".format(
            getDateTime(), reason[:80])
    if order["error_count"] >= MAX_ERROR_COUNT:
        order["status"] = "error"
        order["notes"] = str(order.get("notes", "")) + ";escalated_to_error:{}".format(getDateTime())


def run_auto_trader(session, csv_path, interval_minutes, settings):
    """Background loop that manages marketplace orders.
    Re-reads CSV each cycle for hot-reload support.
    """
    commercial_cities = getCommercialCities(session)
    if not commercial_cities:
        sendToBot(session, "Auto Trader: no city has a Trading Post, stopping.")
        return
    city_lookup = {str(c["id"]): c for c in commercial_cities}

    orders = read_orders(csv_path)
    active_count = len([o for o in orders if o["status"] in ("pending", "active")])
    msg = "Auto Trader started: {} active orders, interval {} min".format(
        active_count, interval_minutes)
    sendToBot(session, msg)

    _update_status(session, orders)

    while True:
        wait(interval_minutes * 60, maxrandom=60)

        # Hot-reload CSV with verification
        _, csv_warnings = verify_csv(csv_path, commercial_cities)
        if csv_warnings:
            warn_msg = "Auto Trader CSV warnings:\n" + "\n".join("  " + w for w in csv_warnings[:10])
            if len(csv_warnings) > 10:
                warn_msg += "\n  ...and {} more".format(len(csv_warnings) - 10)
            sendToBot(session, warn_msg)
        orders = read_orders(csv_path)
        if not any(o["status"] in ("pending", "active") for o in orders):
            sendToBot(session, "Auto Trader: all orders complete or paused.")
            return

        # Activate pending orders
        for o in orders:
            if o["status"] == "pending":
                o["status"] = "active"

        # Reset daily_spent before processing (so budget is fresh for new day)
        _reset_daily_if_new_day(orders)

        # Sort by priority then order_id
        orders.sort(key=lambda o: (o.get("priority", 2), o.get("order_id", 0)))

        # Check action points
        any_html = session.get(city_url + str(commercial_cities[0]["id"]))
        action_points = getActionPoints(any_html)

        # Group orders by city
        city_groups = {}
        for o in orders:
            cid = str(o.get("city_id", ""))
            if cid not in city_groups:
                city_groups[cid] = []
            city_groups[cid].append(o)

        all_notifications = []
        errors_before = {id(o): int(o.get("error_count", 0)) for o in orders}

        # Process each city's orders with per-city error handling
        for cid, city_orders in city_groups.items():
            city = city_lookup.get(cid)
            if not city:
                for o in city_orders:
                    _increment_error(o, "city_not_found")
                continue

            try:
                city = _refresh_city(session, city)
                city_lookup[cid] = city

                # Get gold once per city to share across both modes
                gold, _ = getGold(session, city)

                # Process own_offer orders
                own_offer_orders = [o for o in city_orders if o["mode"] == "own_offer" and o["status"] == "active"]
                if own_offer_orders:
                    _, notifs, gold = process_own_offers(session, city, own_offer_orders, gold)
                    all_notifications.extend(notifs)

                # Process active trading orders
                active_orders = [o for o in city_orders if o["mode"] == "active" and o["status"] == "active"]
                if active_orders and action_points != 0:
                    notifs = process_active_orders(session, city, active_orders, gold)
                    all_notifications.extend(notifs)
                elif active_orders and action_points == 0:
                    all_notifications.append("Skipped active orders - 0 action points")

                # Price tracking
                if settings.get("price_tracking"):
                    price_log_path = get_price_log_path(session)
                    tracked_resources = set()
                    for o in city_orders:
                        if o["status"] == "active":
                            tracked_resources.add(o["resource"])
                    for res_name in tracked_resources:
                        res_idx = _resource_index(res_name)
                        if res_idx < 0:
                            continue
                        lowest, num_sell = _scan_market(session, city, res_idx, TRADE_SELL)
                        highest, num_buy = _scan_market(session, city, res_idx, TRADE_BUY)
                        log_price(price_log_path, res_name, lowest, highest,
                                  num_sell + num_buy, str(city["id"]))

                # Clear the error count only for orders that raised nothing
                # this cycle — otherwise escalation could never reach the
                # threshold.
                for o in city_orders:
                    if o["status"] == "active" and int(o.get("error_count", 0)) == errors_before.get(id(o), 0):
                        o["error_count"] = 0

            except Exception:
                err_msg = "Error processing city {}: {}".format(cid, traceback.format_exc()[-200:])
                all_notifications.append(err_msg)
                for o in city_orders:
                    if o["status"] == "active":
                        _increment_error(o, "city_processing_error")

        # Handle recurring orders
        handle_recurring(orders)

        # Trim notes to prevent unbounded growth
        for o in orders:
            _trim_notes(o)

        # Send notifications
        if all_notifications:
            msg = "Auto Trader:\n" + "\n".join("  " + n for n in all_notifications)
            sendToBot(session, msg)

        # Daily summary
        if settings.get("daily_summary"):
            _maybe_send_daily_summary(session, orders, csv_path)

        # Write updated CSV (protected so a write failure doesn't kill the bot)
        try:
            write_orders(csv_path, orders)
        except Exception:
            sendToBot(session, "Auto Trader: CSV write error: {}".format(
                traceback.format_exc()[-200:]))

        # Update status
        _update_status(session, orders)


# ============================================================
#  Active Trading + Smart Ship Batching
# ============================================================

def _set_resource_filter(session, city, resource_index, browse_type=444):
    """Set marketplace to display offers for a specific resource.
    browse_type: 444 for sell offers (when buying), 333 for buy offers (when selling).
    """
    search_resource = "resource" if resource_index == 0 else str(resource_index)
    data = {
        "cityId": city["id"],
        "position": city["pos"],
        "view": "branchOffice",
        "activeTab": "bargain",
        "type": browse_type,
        "searchResource": search_resource,
        "range": city["rango"],
        "backgroundView": "city",
        "currentCityId": city["id"],
        "templateView": "branchOffice",
        "currentTab": "bargain",
        "actionRequest": actionRequest,
        "ajax": 1,
    }
    session.post(params=data)


def allocate_ships(orders, total_ships, ship_capacity):
    """Allocate ships across orders by priority, then proportionally.
    Returns dict mapping order_id -> allocated_ships.
    """
    if total_ships <= 0 or not orders:
        return {}

    allocation = {}
    remaining_ships = total_ships

    # Group by priority
    priority_groups = {}
    for o in orders:
        p = o.get("priority", 2)
        if p not in priority_groups:
            priority_groups[p] = []
        priority_groups[p].append(o)

    for priority in sorted(priority_groups.keys()):
        if remaining_ships <= 0:
            break
        group = priority_groups[priority]

        # Calculate how many ships each order ideally wants
        wants = {}
        total_want = 0
        for o in group:
            qty = min(o["quantity_remaining"], o["per_cycle"] if o["per_cycle"] > 0 else o["quantity_remaining"])
            ships_needed = max(1, -(-qty // ship_capacity))  # ceil division
            wants[o["order_id"]] = ships_needed
            total_want += ships_needed

        if total_want <= remaining_ships:
            # Enough ships for everyone in this priority
            for o in group:
                alloc = wants[o["order_id"]]
                allocation[o["order_id"]] = alloc
                remaining_ships -= alloc
        else:
            # Proportional split within this priority
            for o in group:
                if remaining_ships <= 0:
                    break
                proportion = wants[o["order_id"]] / total_want if total_want > 0 else 0
                alloc = max(1, int(remaining_ships * proportion))
                alloc = min(alloc, remaining_ships, wants[o["order_id"]])
                allocation[o["order_id"]] = alloc
                remaining_ships -= alloc

    return allocation


def _verify_offer(session, city, offer, res_idx):
    """Re-fetch marketplace offers and verify that the target offer still exists
    with the expected player, price, and availability.
    Returns the refreshed offer dict if valid, or None if stale/gone.
    """
    try:
        _set_resource_filter(session, city, res_idx)
        fresh_offers = getOffers(session, city)
    except Exception:
        return None

    for fresh in fresh_offers:
        if (fresh.get("jugadorAComprar") == offer.get("jugadorAComprar")
                and fresh.get("destinationCityId") == offer.get("destinationCityId")
                and fresh.get("precio") <= offer.get("precio", 0)):
            offer["amountAvailable"] = fresh["amountAvailable"]
            offer["precio"] = fresh["precio"]
            return offer
    return None


def _get_buy_offers(session, city, resource_index):
    """Fetch buy offers (type=333) and return as list of dicts.
    Dict keys match the buy-side offer format so filter functions work on them.
    """
    search_resource = "resource" if resource_index == 0 else str(resource_index)
    data = {
        "cityId": city["id"],
        "position": city["pos"],
        "view": "branchOffice",
        "activeTab": "bargain",
        "type": "333",
        "searchResource": search_resource,
        "range": city["rango"],
        "backgroundView": "city",
        "currentCityId": city["id"],
        "templateView": "branchOffice",
        "currentTab": "bargain",
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    resp = session.post(params=data)
    html = json.loads(resp, strict=False)[1][1][1]

    offers = []
    for row in re.findall(r'<tr[^>]*>([\s\S]*?)</tr>', html):
        city_match = re.search(r'<td class="short_text80">(.*?)<br/>\((.*?)\)', row)
        amount_match = re.search(r'<div class="tooltip">([\d,.\xa0\s]+)</div>', row)
        price_match = re.search(r'<td style="white-space:nowrap;">(\d+)', row)
        speed_match = re.search(r'<td>(\d+)</td>', row)
        dest_match = re.search(r'href="\?view=takeOffer&destinationCityId=(\d+)', row)

        if not all([city_match, amount_match, price_match, dest_match]):
            continue

        raw_amount = re.sub(r'[^\d]', '', amount_match.group(1))
        if not raw_amount:
            continue

        offers.append({
            "ciudadDestino": city_match.group(1).strip(),
            "jugadorAComprar": city_match.group(2).strip(),
            "amountAvailable": int(raw_amount),
            "precio": int(price_match.group(1)),
            "bienesXminuto": int(speed_match.group(1)) if speed_match else 0,
            "destinationCityId": dest_match.group(1),
        })

    offers.sort(key=lambda o: -o["precio"])
    return offers


def _execute_sell(session, city, offer, amount, ships_available, ship_capacity, resource_index):
    """Execute a sell to a buyer's buy offer via sellGoodsAtAnotherBranchOffice."""
    ships_needed = int(math.ceil(Decimal(amount) / Decimal(ship_capacity)))
    ships_used = min(ships_available, ships_needed)
    if ships_needed > ships_used:
        amount = ships_used * ship_capacity

    data = {
        "action": "transportOperations",
        "function": "sellGoodsAtAnotherBranchOffice",
        "cityId": city["id"],
        "destinationCityId": offer["destinationCityId"],
        "oldView": "branchOffice",
        "position": city["pos"],
        "avatar2Name": offer["jugadorAComprar"],
        "city2Name": offer["ciudadDestino"],
        "type": "333",
        "activeTab": "bargain",
        "transportDisplayPrice": "0",
        "premiumTransporter": "0",
        "normalTransportersMax": ships_available,
        "capacity": "5",
        "max_capacity": "5",
        "jetPropulsion": "0",
        "transporters": str(ships_used),
        "backgroundView": "city",
        "currentCityId": city["id"],
        "templateView": "takeOffer",
        "currentTab": "bargain",
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    if resource_index == 0:
        data["cargo_resource"] = amount
        data["resourcePrice"] = offer["precio"]
    else:
        data["tradegood{:d}Price".format(resource_index)] = offer["precio"]
        data["cargo_tradegood{:d}".format(resource_index)] = amount

    session.get(city_url + city["id"], noIndex=True)
    session.post(params=data)
    return amount


def process_active_orders(session, city, orders, gold):
    """Process active buy/sell orders with smart ship batching.
    Uses shared gold pool from caller.
    Returns list of notification strings.
    """
    notifications = []

    ships_available = getAvailableShips(session)
    if ships_available <= 0:
        return []

    ship_capacity, _ = getShipCapacity(session)
    if ship_capacity <= 0:
        return []

    buy_orders = [o for o in orders if o["order_type"] == "buy" and o["quantity_remaining"] > 0]
    sell_orders = [o for o in orders if o["order_type"] == "sell" and o["quantity_remaining"] > 0]

    all_active = buy_orders + sell_orders
    all_active.sort(key=lambda o: (o["priority"], o["order_id"]))

    ship_allocation = allocate_ships(all_active, ships_available, ship_capacity)

    for order in buy_orders:
        alloc_ships = ship_allocation.get(order["order_id"], 0)
        if alloc_ships <= 0:
            continue

        if order["daily_budget"] > 0 and order["daily_spent"] >= order["daily_budget"]:
            continue

        res_idx = _resource_index(order["resource"])
        if res_idx < 0:
            continue

        try:
            _set_resource_filter(session, city, res_idx)
            offers = getOffers(session, city)
        except Exception:
            _increment_error(order, "getOffers_failed")
            continue

        if not offers:
            continue

        if order["strategy"] == "specific" and order.get("target_player"):
            offers = filter_offers_by_player(offers, order["target_player"])
        elif order["strategy"] == "closest":
            offers = sort_offers_by_distance(offers)

        offers = filter_offers_by_max_price(offers, order["price"])

        if not offers:
            continue

        capacity = alloc_ships * ship_capacity
        desired = min(order["quantity_remaining"], capacity)
        if order["per_cycle"] > 0:
            desired = min(desired, order["per_cycle"])

        if order["price"] > 0:
            affordable = gold // order["price"]
            desired = min(desired, affordable)

        if order["daily_budget"] > 0:
            budget_remaining = order["daily_budget"] - order["daily_spent"]
            if order["price"] > 0:
                budget_affordable = budget_remaining // order["price"]
                desired = min(desired, budget_affordable)

        city = _refresh_city(session, city)
        free_space = city["freeSpaceForResources"][res_idx] if res_idx < len(city.get("freeSpaceForResources", [])) else 0
        desired = min(desired, free_space)

        if desired <= 0:
            continue

        amount_bought = 0
        last_offer = None
        for offer in offers:
            if desired <= 0:
                break
            if offer["amountAvailable"] <= 0:
                continue

            if offer["precio"] > order["price"]:
                continue

            # Stale offer protection: re-fetch and verify before purchasing
            verified = _verify_offer(session, city, offer, res_idx)
            if verified is None:
                notifications.append("#{} offer from {} gone/changed, skipping".format(
                    order["order_id"], offer.get("jugadorAComprar", "?")))
                continue

            if verified["amountAvailable"] <= 0:
                continue
            if verified["precio"] > order["price"]:
                notifications.append("#{} price changed to {} (max {}), skipping".format(
                    order["order_id"], verified["precio"], order["price"]))
                continue

            buy_amount = min(desired, verified["amountAvailable"])

            try:
                buy(session, city, verified, buy_amount, alloc_ships, ship_capacity)
                amount_bought += buy_amount
                desired -= buy_amount
                cost = buy_amount * verified["precio"]
                gold -= cost
                order["daily_spent"] += cost
                last_offer = verified
                break
            except Exception:
                _increment_error(order, "buy_failed")
                break

        if amount_bought > 0 and last_offer is not None:
            order["quantity_remaining"] -= amount_bought
            order["quantity_fulfilled"] += amount_bought
            order["last_activity"] = getDateTime()
            notifications.append("#{} Shipped {} {} @ {} from {} | {} remaining".format(
                order["order_id"],
                addThousandSeparator(amount_bought), order["resource"],
                last_offer["precio"], last_offer.get("jugadorAComprar", "?"),
                addThousandSeparator(order["quantity_remaining"])))

            if order["quantity_remaining"] <= 0:
                order["status"] = "complete"
                notifications.append("ORDER #{} COMPLETE: {} {} total".format(
                    order["order_id"], order["order_type"].upper(),
                    addThousandSeparator(order["quantity_fulfilled"])))

    sell_orders.sort(key=lambda o: (o["priority"], o["order_id"]))

    for order in sell_orders:
        alloc_ships = ship_allocation.get(order["order_id"], 0)
        if alloc_ships <= 0:
            continue

        res_idx = _resource_index(order["resource"])
        if res_idx < 0:
            continue

        city = _refresh_city(session, city)
        city_avail = city["availableResources"][res_idx] if res_idx < len(city.get("availableResources", [])) else 0
        if city_avail <= 0:
            continue

        try:
            offers = _get_buy_offers(session, city, res_idx)
        except Exception:
            _increment_error(order, "getBuyOffers_failed")
            continue

        if not offers:
            continue

        # For selling, filter by minimum acceptable price (order["price"])
        offers = [o for o in offers if o["precio"] >= order["price"]]

        if order["strategy"] == "specific" and order.get("target_player"):
            offers = filter_offers_by_player(offers, order["target_player"])
        elif order["strategy"] == "closest":
            offers = sort_offers_by_distance(offers)
        else:
            # Default: sort by highest price first (best deal for seller)
            offers.sort(key=lambda o: -o["precio"])

        if not offers:
            continue

        # Calculate how much we can sell this cycle
        capacity = alloc_ships * ship_capacity
        desired = min(order["quantity_remaining"], capacity, city_avail)
        if order["per_cycle"] > 0:
            desired = min(desired, order["per_cycle"])

        if desired <= 0:
            continue

        amount_sold = 0
        last_offer = None
        for offer in offers:
            if desired <= 0:
                break
            if offer["amountAvailable"] <= 0:
                continue

            # Verify offer still valid
            try:
                fresh_offers = _get_buy_offers(session, city, res_idx)
            except Exception:
                break
            verified = None
            for fresh in fresh_offers:
                if (fresh.get("jugadorAComprar") == offer.get("jugadorAComprar")
                        and fresh.get("destinationCityId") == offer.get("destinationCityId")
                        and fresh.get("precio") >= order["price"]):
                    verified = fresh
                    break
            if verified is None:
                notifications.append("#{} sell offer from {} gone/changed, skipping".format(
                    order["order_id"], offer.get("jugadorAComprar", "?")))
                continue

            sell_amount = min(desired, verified["amountAvailable"])

            try:
                actual_sold = _execute_sell(
                    session, city, verified, sell_amount, alloc_ships, ship_capacity, res_idx)
                amount_sold += actual_sold
                desired -= actual_sold
                last_offer = verified
                break
            except Exception:
                _increment_error(order, "sell_failed")
                break

        if amount_sold > 0 and last_offer is not None:
            order["quantity_remaining"] -= amount_sold
            order["quantity_fulfilled"] += amount_sold
            order["last_activity"] = getDateTime()
            notifications.append("#{} Sold {} {} @ {} to {} | {} remaining".format(
                order["order_id"],
                addThousandSeparator(amount_sold), order["resource"],
                last_offer["precio"], last_offer.get("jugadorAComprar", "?"),
                addThousandSeparator(order["quantity_remaining"])))

            if order["quantity_remaining"] <= 0:
                order["status"] = "complete"
                notifications.append("ORDER #{} COMPLETE: {} {} total".format(
                    order["order_id"], order["order_type"].upper(),
                    addThousandSeparator(order["quantity_fulfilled"])))

    return notifications
