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

# Markup below is copied from the live game (cities Jokios and Lluhios),
# not invented, so these tests fail if the game changes shape.

_OWN_OFFERS_HTML = """
boundariesConfig = {
    'resource': {
        'upper': 10,
        'lower': 4},
    'tradegood1': {
        'upper': 12,
        'lower': 5},
    'tradegood2': {
        'upper': 11,
        'lower': 4},
    'tradegood3': {
        'upper': 50,
        'lower': 18},
    'tradegood4': {
        'upper': 12,
        'lower': 5}};
<input type="text" class="textfield" size="4" name="resource" id="resource" value="518400">
<input type="text" class="textfield" size="2" name="resourcePrice" id="resourcePrice" maxlength="2" value="7">
<td class="select"><select name="resourceTradeType" id="resourceTradeType" size="1" class="dropdown">
  <option value="333">Buy</option>
  <option value="444" selected="">Sell</option>
</select></td>
<input type="text" class="textfield" size="2" name="tradegood1Price" id="tradegood1Price" maxlength="2" value="12">
<select name="tradegood1TradeType" id="tradegood1TradeType" size="1" class="dropdown">
  <option value="333" selected="">Buy</option>
  <option value="444">Sell</option>
</select>
<input type="text" class="textfield" size="2" name="tradegood2Price" id="tradegood2Price" maxlength="2" value="11">
<input type="text" class="textfield" size="2" name="tradegood3Price" id="tradegood3Price" maxlength="2" value="50">
<input type="text" class="textfield" size="2" name="tradegood4Price" id="tradegood4Price" maxlength="2" value="5">
"""


def test_action_point_capacity_parsed(amt):
    html = '<li id="js_GlobalMenu_maxActionPoints" class="actions" title="Action Points">14</li>'
    assert amt.getMaxActionPoints(html) == 14


def test_action_point_capacity_missing_returns_negative(amt):
    """_show_city_info compares with >=, so this must never be None."""
    assert amt.getMaxActionPoints("<html>nothing here</html>") == -1


def test_price_limits_read_boundaries_config(amt):
    """upper prints before lower, and callers unpack (lo, hi)."""
    limits = amt.getPriceLimits(_OWN_OFFERS_HTML)
    assert limits == [(4, 10), (5, 12), (4, 11), (18, 50), (5, 12)]


def test_price_limits_keyed_by_resource_not_position(amt):
    """Crystal is the only slot with an upper of 50."""
    assert amt.getPriceLimits(_OWN_OFFERS_HTML)[3] == (18, 50)


def test_price_limits_fall_back_when_absent(amt):
    assert amt.getPriceLimits("<html></html>") == [(1, 999999)] * 5


def test_own_offer_prices_read_by_field_name(amt):
    """The amount field is name="resource"; the price is name="resourcePrice"."""
    assert amt.getOwnOfferPrices(_OWN_OFFERS_HTML) == [7, 12, 11, 50, 5]


def test_own_offer_price_unparseable_is_none(amt):
    """None signals 'do not guess' to the payload builder."""
    assert amt.getOwnOfferPrices("<html></html>") == [None] * 5


def test_own_offer_trade_types_read_selected_option(amt):
    types = amt.getOwnOfferTradeTypes(_OWN_OFFERS_HTML)
    assert types[0] == amt.TRADE_SELL   # second option carries selected
    assert types[1] == amt.TRADE_BUY    # first option carries selected
    assert types[2] == amt.TRADE_SELL   # no select rendered: default


def test_trade_type_does_not_leak_from_the_next_select(amt):
    """A slot whose select has no selected option must not read the next one."""
    html = (
        '<select name="resourceTradeType"><option value="333">Buy</option></select>'
        '<select name="tradegood1TradeType"><option value="333" selected>Buy</option></select>'
    )
    assert amt.getOwnOfferTradeTypes(html)[0] == amt.TRADE_SELL


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


def test_sort_by_distance_on_the_sell_list_treats_lower_as_nearer(amt):
    """The 333 list reports Distance in squares."""
    offers = [{"jugadorAComprar": "far", "distance": 9},
              {"jugadorAComprar": "near", "distance": 2}]
    assert amt.sort_offers_by_distance(offers)[0]["jugadorAComprar"] == "near"


def test_sort_by_distance_on_the_buy_list_treats_higher_as_nearer(amt):
    """The 444 list has no distance column where ikabot reads; it reports
    goods per minute, which runs the other way."""
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


# ------------------------------------------------- sell-goods offer list

# One real row from the 333 "Sell goods" list. Six cells: no goods-per-minute
# column, an abbreviated amount with the true figure in the tooltip, and a
# Distance cell where lower is nearer.
_SELL_LIST_ROW = """
<tr>
  <td class="short_text80">Irana <br>(Dzohaars)</td>
  <td>2.00M <div class="tooltip" updated="true">2,000,000</div></td>
  <td><img src="//gf2.geo.gfsrv.net/x.png" alt="Building material"></td>
  <td style="white-space:nowrap;">7 <img src="//x.png" class="icon_gold"> Per Piece</td>
  <td>5</td>
  <td><a href="?view=takeOffer&amp;destinationCityId=30485&amp;oldView=branchOffice&amp;activeTab=bargain&amp;cityId=9167&amp;position=9&amp;type=444&amp;resource=resource"><img src="//x.png"></a></td>
</tr>
"""


class OfferListSession:
    def __init__(self, rows):
        self._body = "<table>" + rows + "</table>"

    def post(self, url=None, params=None, **kwargs):
        import json
        return json.dumps([["a", "b"], ["c", ["d", self._body]]])


def test_buy_offer_row_parsed_from_real_markup(amt):
    offers = amt._get_buy_offers(OfferListSession(_SELL_LIST_ROW), _city(), 0)

    assert len(offers) == 1
    offer = offers[0]
    assert offer["ciudadDestino"] == "Irana"
    assert offer["jugadorAComprar"] == "Dzohaars"
    assert offer["amountAvailable"] == 2000000   # from the tooltip, not "2.00M"
    assert offer["precio"] == 7
    assert offer["distance"] == 5
    assert offer["destinationCityId"] == "30485"


def test_buy_offer_amount_survives_extra_tooltip_attributes(amt):
    """updated="true" sits inside the opening div tag."""
    row = _SELL_LIST_ROW.replace('<div class="tooltip" updated="true">',
                                 '<div class="tooltip">')
    plain = amt._get_buy_offers(OfferListSession(row), _city(), 0)
    assert plain[0]["amountAvailable"] == 2000000


def test_buy_offer_accepts_either_br_and_ampersand_form(amt):
    row = _SELL_LIST_ROW.replace("<br>", "<br/>").replace("&amp;", "&")
    offers = amt._get_buy_offers(OfferListSession(row), _city(), 0)
    assert offers[0]["jugadorAComprar"] == "Dzohaars"
    assert offers[0]["destinationCityId"] == "30485"


def test_buy_offers_sorted_best_price_first(amt):
    cheap = _SELL_LIST_ROW.replace('nowrap;">7 ', 'nowrap;">3 ')
    offers = amt._get_buy_offers(OfferListSession(_SELL_LIST_ROW + cheap), _city(), 0)
    assert [o["precio"] for o in offers] == [7, 3]
