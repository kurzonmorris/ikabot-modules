"""Auto Market Trader talks to the marketplace with real gold and real goods.

The module reached this repo referencing eight helpers that were never
defined, so every run died at the first market call. These tests pin the
helpers it needs and the four accounting mistakes that were sitting behind
that crash: an offer left live after its order finished, gold reserved for
goods the warehouse had no room for, buy offers counted against sell
storage, and a demoted order still holding the winner's posted amount.
"""

import importlib.util
import os

import pytest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "modules", "autoMarketTrader_v2.1.0.py",
)


@pytest.fixture(scope="module")
def amt():
    spec = importlib.util.spec_from_file_location("autoMarketTrader", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- parsers

def test_action_points_parsed(amt):
    html = '<span id="js_GlobalMenu_maxActionPoints" class="ap">7</span>'
    assert amt.getActionPoints(html) == 7


def test_action_points_missing_returns_negative(amt):
    """_show_city_info compares with >=, so this must never be None."""
    assert amt.getActionPoints("<html>nothing here</html>") == -1


def test_price_limits_are_min_then_max(amt):
    """The page prints upper before lower; callers unpack (lo, hi)."""
    html = "".join("{'upper': %d, 'lower': %d}," % (100 + i, 10 + i) for i in range(5))
    limits = amt.getPriceLimits(html)
    assert limits[0] == (10, 100)
    assert limits[4] == (14, 104)


def test_price_limits_pads_to_five_slots(amt):
    assert len(amt.getPriceLimits("{'upper': 50, 'lower': 5},")) == 5


def test_own_offer_prices_read_by_field_name(amt):
    html = (
        '<input type="text" name="resourcePrice" value="11"/>'
        '<input type="text" name="tradegood1Price" value="22"/>'
        '<input type="text" name="tradegood2Price" value="33"/>'
        '<input type="text" name="tradegood3Price" value="44"/>'
        '<input type="text" name="tradegood4Price" value="55"/>'
    )
    assert amt.getOwnOfferPrices(html) == [11, 22, 33, 44, 55]


def test_own_offer_price_unparseable_is_none(amt):
    """None signals 'do not guess' to the payload builder."""
    assert amt.getOwnOfferPrices("<html></html>") == [None] * 5


def test_own_offer_trade_types_read_checked_radio(amt):
    html = (
        '<input type="radio" name="resourceTradeType" value="333" checked="checked"/>'
        '<input type="radio" name="tradegood1TradeType" value="444" checked="checked"/>'
    )
    types = amt.getOwnOfferTradeTypes(html)
    assert types[0] == amt.TRADE_BUY
    assert types[1] == amt.TRADE_SELL
    assert types[2] == amt.TRADE_SELL  # absent slots default to sell


# ----------------------------------------------------------- offer filters

def _offer(player, price, speed=0, amount=100):
    return {
        "jugadorAComprar": player,
        "precio": price,
        "bienesXminuto": speed,
        "amountAvailable": amount,
    }


def test_filter_offers_by_player_ignores_case_and_padding(amt):
    offers = [_offer("Kurzon", 10), _offer("Someone", 12)]
    assert amt.filter_offers_by_player(offers, "  kurzon ") == [offers[0]]


def test_filter_offers_by_max_price_keeps_the_limit(amt):
    offers = [_offer("a", 9), _offer("b", 10), _offer("c", 11)]
    assert [o["precio"] for o in amt.filter_offers_by_max_price(offers, 10)] == [9, 10]


def test_sort_offers_by_distance_puts_nearest_first(amt):
    offers = [_offer("far", 10, speed=2), _offer("near", 10, speed=9)]
    assert amt.sort_offers_by_distance(offers)[0]["jugadorAComprar"] == "near"


# --------------------------------------------------- own-offer accounting

class FakeSession:
    def __init__(self):
        self.posted = []

    def post(self, url=None, params=None, **kwargs):
        self.posted.append(params if params is not None else url)
        return "[]"

    def get(self, *args, **kwargs):
        return ""

    def setStatus(self, msg):
        pass


def _city(available=None, free=None):
    return {
        "id": "111",
        "pos": 3,
        "name": "Testopolis",
        "rango": 5,
        "availableResources": available or [5000] * 5,
        "freeSpaceForResources": free or [5000] * 5,
    }


def _order(oid, order_type="sell", resource="Wood", price=10, remaining=500,
           priority=1, last_posted=0):
    order = amt_make_order(oid, resource, order_type, price, remaining, priority)
    order["last_posted"] = last_posted
    return order


def amt_make_order(oid, resource, order_type, price, remaining, priority):
    return {
        "order_id": oid, "priority": priority, "resource": resource,
        "order_type": order_type, "mode": "own_offer", "strategy": "",
        "target_player": "", "price": price, "quantity_remaining": remaining,
        "quantity_fulfilled": 0, "per_cycle": 0, "city_id": "111",
        "city_name": "Testopolis", "undercutting": "no", "recurring": "none",
        "daily_budget": 0, "daily_spent": 0, "daily_reset_date": "",
        "status": "active", "last_activity": "", "last_posted": 0,
        "error_count": 0, "notes": "",
    }


@pytest.fixture
def market(amt, monkeypatch):
    """Control every market read process_own_offers performs."""
    state = {
        "amounts": [0] * 5,
        "types": [amt.TRADE_SELL] * 5,
        "prices": [10] * 5,
        "capacity": 1000,
        "limits": [(1, 100)] * 5,
    }
    monkeypatch.setattr(amt, "getMarketInfo", lambda s, c: "<html/>")
    monkeypatch.setattr(amt, "onSellInMarket", lambda h: list(state["amounts"]))
    monkeypatch.setattr(amt, "getOwnOfferTradeTypes", lambda h: list(state["types"]), raising=False)
    monkeypatch.setattr(amt, "getOwnOfferPrices", lambda h: list(state["prices"]), raising=False)
    monkeypatch.setattr(amt, "storageCapacityOfMarket", lambda h: state["capacity"])
    monkeypatch.setattr(amt, "getPriceLimits", lambda h: list(state["limits"]), raising=False)
    monkeypatch.setattr(amt, "_refresh_city", lambda s, c: c)
    return state


def test_finished_order_clears_its_marketplace_slot(amt, market):
    """A completed order used to leave its offer trading untracked."""
    market["amounts"] = [0, 0, 0, 0, 0]
    order = _order(1, remaining=500, last_posted=500)
    session = FakeSession()

    _, notifications, _ = amt.process_own_offers(session, _city(), [order], 100000)

    assert order["status"] == "complete"
    assert session.posted[-1]["resource"] == "0"
    assert any("COMPLETE" in n for n in notifications)


def test_next_order_takes_over_the_slot(amt, market):
    market["amounts"] = [0, 0, 0, 0, 0]
    done = _order(1, remaining=500, last_posted=500, priority=1)
    waiting = _order(2, remaining=300, priority=2)
    session = FakeSession()

    amt.process_own_offers(session, _city(), [done, waiting], 100000)

    assert done["status"] == "complete"
    assert waiting["status"] == "active"
    assert session.posted[-1]["resource"] == "300"


def test_demoted_order_forgets_the_winners_posted_amount(amt, market):
    """A stale last_posted would be read as the loser's own trades."""
    market["amounts"] = [400, 0, 0, 0, 0]
    winner = _order(1, remaining=500, priority=1, last_posted=400)
    loser = _order(2, remaining=500, priority=2, last_posted=400)
    session = FakeSession()

    amt.process_own_offers(session, _city(), [winner, loser], 100000)

    assert loser["last_posted"] == 0
    assert loser["quantity_fulfilled"] == 0


def test_gold_is_reserved_only_for_what_the_warehouse_can_hold(amt, market):
    """Gold used to be charged before the free-space cap was applied."""
    order = _order(1, order_type="buy", price=10, remaining=1000)
    city = _city(free=[200, 5000, 5000, 5000, 5000])
    session = FakeSession()

    _, _, gold_left = amt.process_own_offers(session, city, [order], 100000)

    assert session.posted[-1]["resource"] == "200"
    assert gold_left == 100000 - 200 * 10


def test_buy_slots_do_not_consume_sell_storage(amt, market):
    """Only goods on sale occupy the trading post."""
    market["capacity"] = 1000
    market["amounts"] = [0, 800, 0, 0, 0]
    market["types"] = [amt.TRADE_SELL, amt.TRADE_BUY, amt.TRADE_SELL,
                       amt.TRADE_SELL, amt.TRADE_SELL]
    order = _order(1, order_type="sell", remaining=900)
    session = FakeSession()

    amt.process_own_offers(session, _city(), [order], 100000)

    assert session.posted[-1]["resource"] == "900"


def test_unmanaged_slot_price_falls_back_safely(amt, market):
    """An unreadable sell price must never be rewritten downward."""
    market["amounts"] = [0, 700, 0, 0, 0]
    market["prices"] = [None] * 5
    market["limits"] = [(5, 250)] * 5
    order = _order(1, order_type="sell", remaining=100)
    session = FakeSession()

    amt.process_own_offers(session, _city(), [order], 100000)

    payload = session.posted[-1]
    assert payload["tradegood1"] == "700"
    assert payload["tradegood1Price"] == "250"
