#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import json
import re
import traceback
from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import *

SATISFACTION_BUFFER = 5


class _StaleCacheError(Exception):
    """Raised when a cached position or token no longer matches the server.

    Triggers cache invalidation + a single retry for the affected city,
    so the bot self-heals when a building is upgraded, demolished, or
    rebuilt between cycles.
    """


class _ApplyRejectedError(Exception):
    """Raised when an apply POST succeeds at the HTTP level but the
    server didn't actually change wineServeLevel.

    The classic trigger is "city has no wine in stock" — Ikariam returns
    a normal-looking ajax response in that case but quietly keeps the
    old level. We detect the mismatch and surface it to the operator
    instead of pretending the change worked.
    """
    def __init__(self, target_level, actual_level):
        self.target_level = target_level
        self.actual_level = actual_level
        if target_level > actual_level:
            msg = (
                f"server kept wine at level {actual_level} — we asked for "
                f"level {target_level}. The city is probably out of wine "
                f"in stock (or producing less than that level requires)."
            )
        elif target_level < actual_level:
            msg = (
                f"server kept wine at level {actual_level} — we asked for "
                f"level {target_level}. The change was rejected."
            )
        else:
            msg = f"server kept wine at level {actual_level} as requested."
        super().__init__(msg)


def _setup_stdio():
    """Make sure stdout/stderr can render non-ASCII city names without
    UnicodeEncodeError on POSIX/C locales or Windows cp1252 terminals.

    Falls back to replacement characters (e.g. 'K?ln' for 'Köln') rather
    than crashing if the terminal really can't encode a character. On
    Python 3.7+ this is one reconfigure call; on older Pythons we wrap
    the underlying buffer in a fresh TextIOWrapper with the same
    semantics. Silent no-op only if both paths fail.
    """
    import io
    for stream_name in ('stdout', 'stderr'):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
            continue
        except (AttributeError, OSError):
            pass
        # Fallback: rebuild a TextIOWrapper around the same buffer.
        try:
            buf = getattr(stream, 'buffer', None)
            if buf is not None:
                line_buf = getattr(stream, 'line_buffering', True)
                setattr(sys, stream_name, io.TextIOWrapper(
                    buf, encoding='utf-8', errors='replace',
                    line_buffering=line_buf,
                ))
        except Exception:
            pass


def _clean_name(name):
    """Normalise a city name for display.

    ikabot's getIdsOfCities strips backslashes before json.loads, which
    leaves names like 'Ku00f6ln' (no backslash, literal escape text)
    instead of the decoded 'Köln'. Some session paths leak the
    backslash form '\\u00f6' instead. Decode whichever shape we get
    so the results table, Telegram messages, and stdout don't show
    raw escape codes — and don't crash with ASCII errors when they
    finally do reach a terminal.
    """
    if not isinstance(name, str):
        return name
    # Backslash-prefixed form: Python's unicode-escape codec handles
    # it cleanly. Try this first because it also covers \n / \t etc.
    if '\\u' in name:
        try:
            return name.encode('utf-8', errors='replace').decode('unicode-escape')
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    # No-backslash form (what getIdsOfCities produces). Same regex
    # ikabot's own decodeUnicodeEscape uses, applied defensively.
    try:
        return re.sub(
            r'u([0-9a-fA-F]{4})',
            lambda m: chr(int(m.group(1), 16)),
            name,
        )
    except Exception:
        return name


class TavernManager:
    def __init__(self, session, notification_mode=3):
        self.session = session
        self.notification_mode = notification_mode
        # Long-lived caches that persist across equilibrium cycles.
        # _positions: city_id -> {'tavern': <building dict>, 'th_pos': int}
        #     Building positions only change on construction/demolition, so
        #     these survive for the bot's lifetime.
        # _cache: city_id -> tavern data {current_level, action_code,
        #     consumption_values, sat_per_wine}
        #     consumption_values and sat_per_wine are static for a given
        #     tavern level; current_level is updated locally after every
        #     successful apply; action_code is a session CSRF token.
        self._positions = {}
        self._cache = {}

    def clear_caches(self, city_id=None):
        """Drop cached data. Pass a city_id to invalidate one city, or
        None to invalidate everything. Use after manual building changes
        you want the next cycle to pick up immediately."""
        if city_id is None:
            self._positions.clear()
            self._cache.clear()
        else:
            self._positions.pop(city_id, None)
            self._cache.pop(city_id, None)

    def _get_positions(self, city_id, force_refresh=False):
        if not force_refresh and city_id in self._positions:
            return self._positions[city_id]
        html = self.session.get(city_url + str(city_id))
        try:
            city_data = getCity(html)
        except Exception:
            return None
        tavern = next(
            (b for b in city_data['position'] if b['building'] == 'tavern'),
            None
        )
        th_pos = next(
            (b['position'] for b in city_data['position'] if b['building'] == 'townHall'),
            None
        )
        info = {'tavern': tavern, 'th_pos': th_pos}
        # If we found neither tavern nor town hall, the city HTML was
        # almost certainly a bad fetch (login redirect, partial page,
        # wrong city in flight). Don't cache the empty result — the
        # next call will retry. Caching it would lock the city out
        # of every subsequent cycle until process restart.
        if tavern is None and th_pos is None:
            return info
        self._positions[city_id] = info
        return info

    def _get_tavern_data(self, city_id, tavern_pos, force_refresh=False):
        if not force_refresh and city_id in self._cache:
            return self._cache[city_id]

        tavern_params = {
            'view': 'tavern',
            'cityId': city_id,
            'position': tavern_pos,
            'backgroundView': 'city',
            'currentCityId': city_id,
            'ajax': '1',
        }
        tavern_response = self.session.post(params=tavern_params)

        try:
            response_data = json.loads(tavern_response)
        except (json.JSONDecodeError, ValueError):
            return None

        current_level = 0
        action_code = None
        consumption_values = []
        sat_per_wine = []

        for item in response_data:
            if not isinstance(item, list) or len(item) < 2:
                continue

            if item[0] == "changeView" and isinstance(item[1], list) and len(item[1]) >= 2:
                tavern_html = item[1][1]
                dropdown_pattern = re.findall(
                    r'<option[^>]*value="(\d+)"[^>]*>(\d+)', tavern_html
                )
                for level_str, wine_str in dropdown_pattern:
                    consumption_values.append((int(level_str), int(wine_str)))

            elif item[0] == "updateTemplateData" and isinstance(item[1], dict):
                load_js = item[1].get("load_js", {})
                if isinstance(load_js, dict) and "params" in load_js:
                    try:
                        js_data = json.loads(load_js["params"])
                        if "wineServeLevel" in js_data:
                            current_level = int(js_data["wineServeLevel"])
                        if "satPerWine" in js_data:
                            sat_per_wine = js_data["satPerWine"]
                    except (json.JSONDecodeError, ValueError, KeyError):
                        pass

            elif item[0] == "updateGlobalData" and isinstance(item[1], dict):
                if "actionRequest" in item[1]:
                    action_code = item[1]["actionRequest"]

        # A valid tavern response always has both the dropdown options
        # (consumption_values) and a CSRF token. If either is missing the
        # cached tavern_pos almost certainly points at a different building
        # now — refuse to cache and signal the caller to refresh.
        if not consumption_values or not action_code:
            raise _StaleCacheError(
                f"city {city_id} tavern@{tavern_pos}: response missing "
                f"{'consumption_values' if not consumption_values else 'action_code'}"
            )

        result = {
            'current_level': current_level,
            'action_code': action_code,
            'consumption_values': consumption_values,
            'sat_per_wine': sat_per_wine,
        }
        self._cache[city_id] = result
        return result

    def _wine_at_level(self, consumption_values, level):
        return next((w for l, w in consumption_values if l == level), 0)

    def _apply_level(self, city_id, tavern_pos, target_level, tavern_data):
        params = {
            'action': 'CityScreen',
            'function': 'assignWinePerTick',
            'cityId': city_id,
            'position': tavern_pos,
            'amount': target_level,
            'backgroundView': 'city',
            'currentCityId': city_id,
            'templateView': 'tavern',
            'actionRequest': tavern_data['action_code'],
            'ajax': '1',
        }
        response = self.session.post(params=params)
        if not self._apply_response_ok(response):
            raise _StaleCacheError(
                f"city {city_id} apply L{target_level} rejected — "
                f"likely stale position or CSRF token"
            )

        # Ikariam will quietly refuse to raise wineServeLevel when the
        # city has no wine in stock — HTTP 200, no error item, but the
        # level didn't actually change. So don't just trust the POST:
        # read back the level the server now has, and if it doesn't
        # match what we asked for, treat it as a rejected apply.
        actual_level = self._extract_applied_level(response)
        if actual_level is None:
            # Apply response didn't include the new level; refresh to
            # find out. The wasted POST is only paid on actual changes.
            try:
                verify = self._get_tavern_data(city_id, tavern_pos, force_refresh=True)
                actual_level = verify['current_level'] if verify else None
            except _StaleCacheError:
                actual_level = None

        # Sync the cache to whatever the server actually has (or, if
        # we couldn't determine it, optimistically to target_level).
        if city_id in self._cache:
            self._cache[city_id]['current_level'] = (
                actual_level if actual_level is not None else target_level
            )

        if actual_level is not None and actual_level != target_level:
            raise _ApplyRejectedError(target_level, actual_level)

    @staticmethod
    def _apply_response_ok(response):
        """Lenient apply-response check: only declare failure on clear
        error markers. If the response is not parseable JSON or contains
        an ['error', ...] item, treat it as failure."""
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, ValueError, TypeError):
            return False
        if not isinstance(data, list):
            return False
        for item in data:
            if isinstance(item, list) and item and item[0] == 'error':
                return False
        return True

    @staticmethod
    def _extract_applied_level(response):
        """Parse an apply response for the post-apply wineServeLevel.
        Returns the integer level or None if the response didn't
        include one."""
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
        if not isinstance(data, list):
            return None
        for item in data:
            if not (isinstance(item, list) and len(item) >= 2):
                continue
            if item[0] == 'updateTemplateData' and isinstance(item[1], dict):
                load_js = item[1].get('load_js', {})
                if isinstance(load_js, dict) and 'params' in load_js:
                    try:
                        js_data = json.loads(load_js['params'])
                        if 'wineServeLevel' in js_data:
                            return int(js_data['wineServeLevel'])
                    except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                        pass
        return None

    def _get_town_hall_data(self, city_id, th_pos):
        if th_pos is None:
            return None

        th_params = {
            'view': 'townHall',
            'cityId': city_id,
            'position': th_pos,
            'backgroundView': 'city',
            'currentCityId': city_id,
            'ajax': '1',
        }
        th_response = self.session.post(params=th_params)

        try:
            th_data = json.loads(th_response)
        except (json.JSONDecodeError, ValueError):
            return None

        th_html = next(
            (item[1][1] for item in th_data
             if isinstance(item, list) and item[0] == "changeView"
             and isinstance(item[1], list) and len(item[1]) >= 2),
            None
        )
        if not th_html:
            return None

        growth_match = re.search(r'id="js_TownHallPopulationGrowthValue">([0-9,.\xa0\s-]+)', th_html)
        satisfaction_match = re.search(
            r'id="js_TownHallHappinessLargeValue"[^>]*>([0-9,.\xa0\s-]+)', th_html
        )
        occupied_match = re.search(
            r'id="js_TownHallOccupiedSpace"[^>]*>([0-9,.\xa0\s]+)', th_html
        )
        max_inhabitants_match = re.search(
            r'id="js_TownHallMaxInhabitants"[^>]*>([0-9,.\xa0\s]+)', th_html
        )

        if not growth_match or not satisfaction_match:
            # The server returned a view but it isn't the Town Hall —
            # cached th_pos is pointing at the wrong building.
            raise _StaleCacheError(
                f"city {city_id} townHall@{th_pos}: response is not the town hall view"
            )

        resource_shortage = bool(
            re.search(r'class="[^"]*shortage[^"]*"', th_html, re.IGNORECASE)
        )

        return {
            'growth_rate': self._parse_decimal(growth_match.group(1)),
            'total_satisfaction': self._parse_signed_int(satisfaction_match.group(1)),
            'resource_shortage': resource_shortage,
            'current_pop': self._parse_int(occupied_match.group(1)) if occupied_match else None,
            'max_pop': self._parse_int(max_inhabitants_match.group(1)) if max_inhabitants_match else None,
        }

    @staticmethod
    def _parse_int(s):
        digits = re.sub(r'\D', '', s)
        return int(digits) if digits else 0

    @staticmethod
    def _parse_signed_int(s):
        # Integer that may carry a sign and thousands separators; e.g. "-1,234"
        negative = '-' in s
        digits = re.sub(r'\D', '', s)
        if not digits:
            return 0
        n = int(digits)
        return -n if negative else n

    @staticmethod
    def _parse_decimal(s):
        s = s.strip().replace('\xa0', '').replace(' ', '')
        if not s or s in ('-', '.'):
            return 0.0
        # Decide which separator is the decimal mark: whichever comes last.
        last_dot = s.rfind('.')
        last_comma = s.rfind(',')
        if last_comma > last_dot:
            # comma is decimal mark
            s = s.replace('.', '').replace(',', '.')
        else:
            # dot is decimal mark (or no decimal mark)
            s = s.replace(',', '')
        try:
            return float(s)
        except ValueError:
            return 0.0

    def _optimize_for_satisfaction(self, city_id, tavern_pos, tavern_data, current_satisfaction):
        sat_per_wine = tavern_data['sat_per_wine']
        current_level = tavern_data['current_level']
        consumption_values = tavern_data['consumption_values']

        if not sat_per_wine:
            return None

        if current_level < len(sat_per_wine):
            base_satisfaction = current_satisfaction - sat_per_wine[current_level]
        else:
            base_satisfaction = current_satisfaction

        optimal_level = current_level
        for level in range(len(sat_per_wine)):
            projected = base_satisfaction + sat_per_wine[level]
            if projected >= SATISFACTION_BUFFER:
                optimal_level = level
                break

        if optimal_level == current_level:
            return None

        current_wine = self._wine_at_level(consumption_values, current_level)
        new_wine = self._wine_at_level(consumption_values, optimal_level)
        self._apply_level(city_id, tavern_pos, optimal_level, tavern_data)
        return f"L{current_level}({current_wine}/h)→L{optimal_level}({new_wine}/h)"

    def _process_one_city(self, city_id, city):
        """Run the equilibrium decision for a single city. Returns the
        result dict. May raise _StaleCacheError if the cached positions
        or token no longer match the server — the caller will refresh
        and retry.

        _ApplyRejectedError (apply succeeded HTTP-wise but the server
        kept the old wine level — usually because the city is out of
        wine) is caught locally so we still surface the population /
        happiness numbers we already fetched."""
        try:
            return self._process_one_city_inner(city_id, city)
        except _ApplyRejectedError as rejected:
            # We already filled in pop/sat on the partial result that
            # was lost when the exception unwound — rebuild a WARN row
            # with as much context as we have.
            return {
                'name': _clean_name(city.get('name', '')), 'status': 'WARN',
                'pop': '', 'satisfaction': '',
                'note': f"wine NOT changed — {rejected}",
            }

    def _process_one_city_inner(self, city_id, city):
        # Decode any literal unicode-escape sequences left in the
        # city name by ikabot's session-data parser, so the results
        # table prints "Köln" rather than "Ku00f6ln" and stdout
        # doesn't trip over non-ASCII characters mid-row.
        display_name = _clean_name(city.get('name', ''))
        result = {
            'name': display_name,
            'status': None,
            'pop': '',
            'satisfaction': '',
            'note': '',
        }

        positions = self._get_positions(city_id)
        if positions is None:
            result.update({'status': 'SKIP', 'note': "couldn't read city — will retry next cycle"})
            return result

        tavern = positions['tavern']
        th_pos = positions['th_pos']

        if not tavern:
            result.update({'status': 'SKIP', 'note': 'no tavern in this city'})
            return result

        th_data = self._get_town_hall_data(city_id, th_pos)
        if not th_data:
            result.update({'status': 'SKIP', 'note': "couldn't read town hall — will retry next cycle"})
            return result

        current_pop = th_data['current_pop']
        max_pop = th_data['max_pop']
        # max_pop = 0 means the parse hit a non-numeric span — treat as
        # unreadable rather than letting "0 < max_pop" decide we should
        # blast wine to max.
        if current_pop is None or not max_pop:
            result.update({'status': 'SKIP', 'note': "couldn't read population — will retry next cycle"})
            return result
        result['pop'] = f"{current_pop}/{max_pop}"

        tavern_data = self._get_tavern_data(city_id, tavern['position'])
        if not tavern_data or not tavern_data['action_code']:
            result.update({'status': 'SKIP', 'note': "couldn't read tavern — will retry next cycle"})
            return result

        tavern_pos = tavern['position']

        if current_pop < max_pop:
            consumption_values = tavern_data['consumption_values']
            max_level = max((l for l, w in consumption_values), default=tavern['level'])
            current_level = tavern_data['current_level']

            if current_level == max_level:
                result.update({'status': 'OK', 'note': 'still growing, wine already at MAX'})
            else:
                current_wine = self._wine_at_level(consumption_values, current_level)
                new_wine = self._wine_at_level(consumption_values, max_level)
                self._apply_level(city_id, tavern_pos, max_level, tavern_data)
                result.update({
                    'status': 'CHANGED',
                    'note': (
                        f"still growing — wine raised to MAX: "
                        f"L{current_level} ({current_wine}/h) -> "
                        f"L{max_level} ({new_wine}/h)"
                    ),
                })
            return result

        growth_rate = th_data['growth_rate']
        total_satisfaction = th_data['total_satisfaction']
        result['satisfaction'] = str(total_satisfaction)

        if growth_rate > 0:
            result.update({'status': 'OK', 'note': f"happy + growing ({growth_rate:+.1f}/h)"})
            return result

        if th_data['resource_shortage']:
            result.update({
                'status': 'WARN',
                'note': 'growth stalled by a resource shortage — wine left unchanged',
            })
            return result

        if total_satisfaction < SATISFACTION_BUFFER:
            result.update({
                'status': 'WARN',
                'note': (
                    f"happiness {total_satisfaction} is below the safety buffer "
                    f"of +{SATISFACTION_BUFFER}; keeping wine where it is"
                ),
            })
            return result

        change = self._optimize_for_satisfaction(city_id, tavern_pos, tavern_data, total_satisfaction)
        if change:
            result.update({'status': 'CHANGED', 'note': f"wine lowered: {change}"})
        else:
            result.update({'status': 'OK', 'note': 'wine already at the lowest safe level'})
        return result

    def process_equilibrium(self, cities_ids, cities):
        results = []

        for city_id in cities_ids:
            city = cities[city_id]
            # Sanitize the display name once per iteration so error
            # rows, ERROR-result dicts and Telegram strings all use
            # the human-readable form rather than the raw escape
            # sequence from getIdsOfCities.
            display_name = _clean_name(city.get('name', ''))
            try:
                result = self._process_one_city(city_id, city)
            except _StaleCacheError as stale:
                # Something on the server moved out from under us
                # (building upgrade/demolition, token expiry, etc.).
                # Drop this city's caches and try one more time with
                # freshly fetched positions and tavern data.
                self.clear_caches(city_id)
                try:
                    result = self._process_one_city(city_id, city)
                    note = result.get('note', '')
                    result['note'] = f"{note} (refreshed)".strip()
                except _StaleCacheError as stale2:
                    result = {
                        'name': display_name, 'status': 'ERROR',
                        'pop': '', 'satisfaction': '',
                        'note': (
                            "city looked stale, still couldn't read it after a "
                            f"refresh — building may have changed ({stale2})"
                        ),
                    }
                    if self.notification_mode in (1, 2):
                        sendToBot(
                            self.session,
                            f"❌ Wine auto-tune error\n{display_name}: still stale after refresh ({stale2})"
                        )
                except Exception as e:
                    self.clear_caches(city_id)
                    result = {
                        'name': display_name, 'status': 'ERROR',
                        'pop': '', 'satisfaction': '',
                        'note': str(e),
                    }
                    if self.notification_mode in (1, 2):
                        sendToBot(self.session, f"❌ Wine auto-tune error\n{display_name}: {str(e)}")
            except Exception as e:
                self.clear_caches(city_id)
                result = {
                    'name': display_name, 'status': 'ERROR',
                    'pop': '', 'satisfaction': '',
                    'note': str(e),
                }
                if self.notification_mode in (1, 2):
                    sendToBot(self.session, f"❌ Wine auto-tune error\n{display_name}: {str(e)}")

            results.append(result)

        changes = [r for r in results if r['status'] == 'CHANGED']
        if self.notification_mode == 1 and changes:
            msg = "🍷 Wine auto-tune — changes this cycle\n\n"
            for r in changes:
                msg += f"📍 {r['name']}: {r['note']}\n"
            sendToBot(self.session, msg)

        # Out-of-wine WARN rows are operationally important even when
        # the operator only asked for "errors only" — flag them too.
        wine_blocked = [
            r for r in results
            if r['status'] == 'WARN' and 'wine NOT changed' in r['note']
        ]
        if wine_blocked and self.notification_mode in (1, 2):
            msg = "⚠️ Wine auto-tune — couldn't apply wine in some cities\n\n"
            for r in wine_blocked:
                msg += f"📍 {r['name']}: {r['note']}\n"
            sendToBot(self.session, msg)

        return results

    def set_tavern_pct(self, city, pct):
        """Set this city's wine consumption to `pct`% of the tavern's
        maximum available level, rounded to the nearest integer level
        the dropdown actually offers. pct is an int in [0, 100]."""
        display_name = _clean_name(city.get('name', ''))
        try:
            return self._set_tavern_pct_once(city, pct, display_name)
        except _ApplyRejectedError as rejected:
            # No retry: an "out of wine" rejection won't be fixed by
            # trying again, so report it and move on.
            print(f"  {display_name}: NOT CHANGED — {rejected}")
            return False
        except _StaleCacheError as stale:
            # Force-refresh paths above already fetched fresh data, so a
            # _StaleCacheError here means the server rejected the apply
            # (e.g. stale session token). Clear and retry once.
            self.clear_caches(city['id'])
            try:
                ok = self._set_tavern_pct_once(city, pct, display_name)
                if ok:
                    print(f"  {display_name}: (cached data was stale, refreshed and retried successfully)")
                return ok
            except _ApplyRejectedError as rejected:
                print(f"  {display_name}: NOT CHANGED — {rejected}")
                return False
            except _StaleCacheError as stale2:
                print(
                    f"  {display_name}: failed — cached data was stale and the "
                    f"refresh didn't help ({stale2})"
                )
                return False

    def _set_tavern_pct_once(self, city, pct, display_name=None):
        city_id = city['id']
        if display_name is None:
            display_name = _clean_name(city.get('name', ''))

        # Manual mode: always refresh so the user sees current state.
        positions = self._get_positions(city_id, force_refresh=True)
        if positions is None:
            print(f"  {display_name}: skipped — couldn't read city data from the server")
            return False

        tavern = positions['tavern']
        if not tavern:
            print(f"  {display_name}: skipped — no tavern built in this city")
            return False

        tavern_data = self._get_tavern_data(city_id, tavern['position'], force_refresh=True)
        if not tavern_data or not tavern_data['action_code']:
            print(f"  {display_name}: skipped — couldn't read tavern data from the server")
            return False

        consumption_values = tavern_data['consumption_values']
        current_level = tavern_data['current_level']

        if not consumption_values:
            print(f"  {display_name}: skipped — tavern has no wine settings available")
            return False

        max_level = max(l for l, w in consumption_values)
        # Map percentage -> nearest integer level in the dropdown.
        available_levels = sorted({l for l, _ in consumption_values})
        target_raw = pct * max_level / 100.0
        target_level = min(available_levels, key=lambda l: (abs(l - target_raw), l))

        current_wine = self._wine_at_level(consumption_values, current_level)
        new_wine = self._wine_at_level(consumption_values, target_level)
        if target_level == current_level:
            print(f"  {display_name}: already at level {current_level} ({current_wine} wine/hr) — no change needed")
            return True
        self._apply_level(city_id, tavern['position'], target_level, tavern_data)
        print(
            f"  {display_name}: set to {pct}% — "
            f"level {current_level} ({current_wine} wine/hr) -> "
            f"level {target_level} ({new_wine} wine/hr)"
        )
        return True


def _print_results_table(results):
    col_city = max((len(r['name']) for r in results), default=8)
    col_city = max(col_city, 8)

    status_colors = {
        'OK':      bcolors.GREEN,
        'CHANGED': bcolors.BLUE,
        'WARN':    bcolors.WARNING,
        'SKIP':    bcolors.STONE,
        'ERROR':   bcolors.RED,
    }

    header = f"{'City':<{col_city}}  {'Result':<7}  {'Pop':<11}  {'Happy':>5}  What happened"
    divider = "─" * (col_city + 7 + 11 + 5 + 4 * 4 + 30)

    print()
    print(header)
    print(divider)

    for r in results:
        color = status_colors.get(r['status'], '')
        status_str = f"{color}{r['status']:<7}{bcolors.ENDC}" if color else f"{r['status']:<7}"
        print(f"{r['name']:<{col_city}}  {status_str}  {r['pop']:<11}  {r['satisfaction']:>5}  {r['note']}")

    print(divider)
    changed = sum(1 for r in results if r['status'] == 'CHANGED')
    ok      = sum(1 for r in results if r['status'] == 'OK')
    warned  = sum(1 for r in results if r['status'] in ('WARN', 'ERROR', 'SKIP'))
    # Plain-English legend so first-timers don't have to guess.
    print(
        f"{bcolors.GREEN}OK     = nothing to change{bcolors.ENDC}   "
        f"{bcolors.BLUE}CHANGED = wine level adjusted{bcolors.ENDC}"
    )
    print(
        f"{bcolors.WARNING}WARN   = something off, no change made{bcolors.ENDC}   "
        f"{bcolors.STONE}SKIP   = couldn't read this city{bcolors.ENDC}   "
        f"{bcolors.RED}ERROR  = failed{bcolors.ENDC}"
    )
    print(
        f"Summary: {bcolors.BLUE}{changed} changed{bcolors.ENDC}, "
        f"{bcolors.GREEN}{ok} already fine{bcolors.ENDC}, "
        f"{bcolors.WARNING}{warned} skipped/warned/errored{bcolors.ENDC}"
    )
    print()


def tavernManager(session, event, stdin_fd, predetermined_input):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    event : multiprocessing.Event
    stdin_fd: int
    predetermined_input : multiprocessing.managers.SyncManager.list
    """
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    _setup_stdio()

    try:
        banner()

        print("=" * 60)
        print("TAVERN WINE MANAGER")
        print("=" * 60)
        print()
        print("Your tavern serves wine to keep citizens happy. More wine")
        print("= more happiness = faster population growth, but it also")
        print("burns through your wine stockpile.")
        print()
        print("Pick what you want to do:")
        print()
        print("  (1) Set a wine level by hand")
        print("      Apply a fixed percentage (0-100%) to chosen cities.")
        print("      Good for one-off changes or emergencies.")
        print()
        print("  (2) Auto-tune wine (runs in the background)")
        print("      Sets MAX wine while cities are still growing, then")
        print("      drops wine to the lowest level that keeps them happy")
        print("      once they hit max population.")
        print()
        print("  (') Back to the main ikabot menu")
        print()

        mode = read(msg="Pick 1 or 2: ", min=1, max=2, digit=True, additionalValues=["'"])

        if mode == "'":
            event.set()
            return

        print()

        if mode == 1:
            _run_set_mode(session)
        else:
            _run_equilibrium_mode(session, event, stdin_fd, predetermined_input)

    except KeyboardInterrupt:
        pass

    event.set()


def _run_set_mode(session):
    print("=" * 60)
    print("SET WINE LEVEL BY HAND")
    print("=" * 60)
    print()
    print("Two quick questions: which cities, and what percentage.")
    print()

    cities_to_process = _select_cities(session)
    if cities_to_process is None:
        return
    if not cities_to_process:
        print("No cities chosen — nothing to do.")
        print()
        enter()
        return

    pct = _select_percentage()
    if pct is None:
        return

    print()
    city_word = "city" if len(cities_to_process) == 1 else "cities"
    print(f"About to set wine to {pct}% in {len(cities_to_process)} {city_word}.")
    print()
    confirm = read(msg="Go ahead? [Y/n]: ", values=["y", "Y", "n", "N", ""])
    if confirm.lower() == "n":
        print("Cancelled, nothing was changed.")
        print()
        enter()
        return

    print()
    print("Working through your cities...")
    print()

    mgr = TavernManager(session)
    success_count = sum(1 for city in cities_to_process if mgr.set_tavern_pct(city, pct))

    print()
    print(f"{bcolors.GREEN}Done.{bcolors.ENDC}")
    print(f"{success_count} of {len(cities_to_process)} cities updated successfully.")
    if success_count < len(cities_to_process):
        print("(Cities that failed are listed above with the reason.)")
    print()
    enter()


def _select_cities(session):
    """Prompt for city selection. Returns a list of city dicts (each
    with at least 'id' and 'name'), [] if the user picked an empty
    set, or None if they backed out."""
    print("Which cities should this apply to?")
    print()
    print("  (1) Just one city")
    print("      Pick a single city from a list.")
    print()
    print("  (2) Some of my cities")
    print("      Start with every city, then remove the ones you")
    print("      don't want included.")
    print()
    print("  (3) Every city I own")
    print()
    print("  (') Go back")
    print()
    choice = read(msg="Pick 1, 2, or 3: ", min=1, max=3, digit=True, additionalValues=["'"])
    if choice == "'":
        return None

    print()

    if choice == 1:
        city = chooseCity(session)
        return [city] if city else []
    if choice == 2:
        cities_ids, cities = ignoreCities(
            session,
            msg="Type a number to remove that city, then 0 when you're done:",
        )
        return [cities[cid] for cid in cities_ids]
    cities_ids, cities = getIdsOfCities(session)
    return [cities[cid] for cid in cities_ids]


def _select_percentage():
    """Prompt for a wine consumption percentage. Returns an int in
    [0, 100], or None if the user backed out."""
    pct_options = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    manual_idx = len(pct_options) + 1

    print("How much wine should each tavern serve?")
    print()
    print("  0%   = no wine (citizens get unhappy, population can shrink)")
    print("  100% = the highest setting your tavern allows (most growth,")
    print("         most wine consumed per hour)")
    print()
    print("Pick a percentage:")
    print()
    for i, p in enumerate(pct_options, 1):
        if p == 0:
            suffix = "  (no wine)"
        elif p == 100:
            suffix = "  (maximum)"
        elif p == 50:
            suffix = "  (half)"
        else:
            suffix = ""
        print(f"  ({i:>2}) {p:>3}%{suffix}")
    print(f"  ({manual_idx:>2}) Type a custom number")
    print()
    print("  (') Go back")
    print()

    choice = read(
        msg=f"Pick 1-{manual_idx}: ",
        min=1, max=manual_idx, digit=True,
        additionalValues=["'"],
    )
    if choice == "'":
        return None

    if choice == manual_idx:
        print()
        return read(msg="Enter a percentage from 0 to 100: ", min=0, max=100, digit=True)
    return pct_options[choice - 1]


def _run_equilibrium_mode(session, event, stdin_fd, predetermined_input):
    print("=" * 60)
    print("AUTO-TUNE WINE")
    print("=" * 60)
    print()
    print("How auto-tune decides what to do for each city:")
    print()
    print("  - Population still growing? -> serve MAX wine (fastest")
    print("    growth).")
    print("  - Population at the cap and citizens are happy? -> drop")
    print(f"    wine to the lowest level that keeps happiness above +{SATISFACTION_BUFFER}")
    print("    so you stop wasting wine.")
    print("  - Growth stuck because you're short on a resource? -> leave")
    print("    wine alone and flag the city so you can investigate.")
    print()
    print("Three quick questions: Telegram alerts, which cities, schedule.")
    print()
    print("-" * 60)
    print("Question 1 of 3: Telegram alerts")
    print("-" * 60)
    print()
    print("Should the bot text you on Telegram?")
    print("(Needs Telegram set up in ikabot already — otherwise just")
    print("pick option 3.)")
    print()
    print("  (1) Every wine change AND every error")
    print("      Most notifications. Good when you're tuning settings.")
    print("  (2) Only when something goes wrong")
    print("      Quiet most of the time; pings you if a city errors.")
    print("  (3) Don't text me")
    print()
    print("  (') Go back")
    print()
    notification_mode = read(msg="Pick 1, 2, or 3: ", min=1, max=3, digit=True, additionalValues=["'"])

    if notification_mode == "'":
        return

    print()
    print("-" * 60)
    print("Question 2 of 3: Which cities")
    print("-" * 60)
    print()
    print("Which cities should auto-tune?")
    print()
    print("  (1) Choose a subset")
    print("      Start with every city, then remove the ones you don't")
    print("      want auto-tuned.")
    print("  (2) Every city I own")
    print()
    print("  (') Go back")
    print()
    city_choice = read(msg="Pick 1 or 2: ", min=1, max=2, digit=True, additionalValues=["'"])

    if city_choice == "'":
        return

    print()

    if city_choice == 1:
        cities_ids, cities = ignoreCities(
            session,
            msg="Type a number to remove that city, then 0 when you're done:",
        )
    else:
        cities_ids, cities = getIdsOfCities(session)

    print()
    print("-" * 60)
    print("Question 3 of 3: Schedule")
    print("-" * 60)
    print()
    print("Run once now, or keep running on a schedule?")
    print()
    print("    0     Run a single check right now, then exit")
    print("  1-24    Run continuously in the background, checking")
    print("          every N hours")
    print()
    print("  (') Go back")
    print()
    run_hours = read(
        msg="Enter a number from 0 to 24: ",
        min=0, max=24, digit=True, additionalValues=["'"]
    )

    if run_hours == "'":
        return

    print()

    mgr = TavernManager(session, notification_mode)

    def run_check():
        results = mgr.process_equilibrium(cities_ids, cities)
        _print_results_table(results)
        return results

    if run_hours == 0:
        print(f"Checking {len(cities_ids)} cities once...")
        print()
        run_check()
        enter()
        return

    print(f"Auto-tune will check {len(cities_ids)} cities every {run_hours} hour(s).")
    print("Press Ctrl+C in the parent ikabot to stop it.")
    print()
    print("Running the first check now (so you can see the result")
    print("before it disappears into the background)...")
    print()
    run_check()
    enter()

    set_child_mode(session)
    event.set()

    info = f"\nWine auto-tune: {len(cities_ids)} cities, every {run_hours}h\n"
    setInfoSignal(session, info)

    try:
        while True:
            wait(run_hours * 3600)
            # Per-cycle error firewall: a transient failure inside a
            # single cycle (network blip, table-print bug, anything
            # process_equilibrium didn't catch internally) must NOT kill
            # the daemon. Print it, optionally telegram it, and continue.
            try:
                run_check()
                session.setStatus(f"Wine auto-tune ran at {getDateTime()}")
            except KeyboardInterrupt:
                raise
            except Exception:
                cycle_err = (
                    f"Wine auto-tune had a problem this cycle, but it will keep running.\n"
                    f"Task: {info}"
                    f"Details:\n{traceback.format_exc()}"
                )
                traceback.print_exc()
                if notification_mode in (1, 2):
                    try:
                        sendToBot(session, cycle_err)
                    except Exception:
                        traceback.print_exc()
    except KeyboardInterrupt:
        pass
    except Exception:
        # Fatal: wait() or some non-cycle code raised. Surface it
        # locally and via telegram before logging out.
        msg = (
            f"Wine auto-tune stopped because of a fatal error.\n"
            f"Task: {info}"
            f"Details:\n{traceback.format_exc()}"
        )
        traceback.print_exc()
        if notification_mode in (1, 2):
            try:
                sendToBot(session, msg)
            except Exception:
                traceback.print_exc()
    finally:
        session.logout()
