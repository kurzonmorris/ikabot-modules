#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import json
import re
import csv
import time
import pathlib
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

# ============================================================================
#  PERSISTENCE LAYER  — saved task state, lock, stop signal
# ============================================================================
#
# Layout under ~/ (one set of files per ikabot account):
#   .ikabot_tavern_<server>_<user>.csv            saved per-city task rows
#   .ikabot_tavern_<server>_<user>.csv.lock       advisory write lock
#   .ikabot_tavern_worker_<server>_<user>.lock    held while a run is active
#   .ikabot_tavern_stop_<server>_<user>           stop signal (just a flag file)
#
# Each CSV row is one city's task entry. Columns split into three groups:
#   - identity:    city_id, city_name
#   - directive:   mode/pct/interval_hours/notification_mode  (what to do)
#   - lifecycle:   status/created_at/last_run/next_run/total_runs/notes
#   - cached:      tavern_pos / tavern_level / max_wine_level /
#                  sat_per_wine_json / consumption_values_json /
#                  last_applied_level
#
# Cached fields are populated on the first successful cycle. After that the
# scheduled runs skip the "fetch positions + tavern view" round-trips and
# only refetch the town hall (the only truly dynamic piece). The full check
# is reserved for brand-new tasks.

TAVERN_SCHEMA_VERSION = 1

TAVERN_CSV_COLUMNS = [
    'city_id', 'city_name',
    'mode', 'pct',
    'interval_hours', 'notification_mode',
    'status',
    'created_at', 'last_run', 'next_run', 'total_runs',
    'tavern_pos', 'tavern_level', 'max_wine_level',
    'sat_per_wine_json', 'consumption_values_json',
    'last_applied_level',
    'notes',
    'schema_version',
]

# Coerce types when reading CSV strings back into Python.
_INT_COLS = {
    'interval_hours', 'notification_mode',
    'created_at', 'total_runs',
    'tavern_pos', 'tavern_level', 'max_wine_level',
    'pct', 'last_applied_level',
    'schema_version',
}
_INT_OR_BLANK_COLS = {'last_run', 'next_run', 'pct', 'last_applied_level'}
_JSON_COLS = {'sat_per_wine_json', 'consumption_values_json'}

VALID_MODES = ('set', 'auto')
VALID_STATUSES = ('pending', 'active', 'paused', 'completed', 'error')


def _safe(value):
    return re.sub(r'[^\w.-]', '_', str(value))


def _account_suffix(session):
    return f"{_safe(session.servidor)}_{_safe(session.username)}"


def _tavern_csv_path(session):
    return os.path.join(
        os.path.expanduser('~'),
        f".ikabot_tavern_{_account_suffix(session)}.csv",
    )


def _tavern_csv_lock_path(session):
    return _tavern_csv_path(session) + '.lock'


def _tavern_worker_lock_path(session):
    return os.path.join(
        os.path.expanduser('~'),
        f".ikabot_tavern_worker_{_account_suffix(session)}.lock",
    )


def _tavern_stop_flag_path(session):
    return os.path.join(
        os.path.expanduser('~'),
        f".ikabot_tavern_stop_{_account_suffix(session)}",
    )


# ---- File locks (advisory; uses a sentinel file with stale detection) ----

def _lock_acquire(lock_path, timeout=30, stale_after=300):
    """Acquire a lock by creating the lockfile with O_EXCL. If the file is
    older than `stale_after` seconds it's considered abandoned and forced
    open. Returns True on success."""
    start = time.time()
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {int(time.time())}\n".encode('utf-8'))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path)
                if age > stale_after:
                    # Probably a crashed process. Take it.
                    try:
                        os.remove(lock_path)
                    except OSError:
                        pass
                    continue
            except OSError:
                pass
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)


def _lock_release(lock_path):
    try:
        os.remove(lock_path)
    except OSError:
        pass


class _csv_lock:
    """Context manager around the CSV's lockfile."""
    def __init__(self, session):
        self.path = _tavern_csv_lock_path(session)

    def __enter__(self):
        if not _lock_acquire(self.path, timeout=30, stale_after=60):
            raise RuntimeError(f"Could not acquire tavern CSV lock at {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb):
        _lock_release(self.path)


# ---- Row type coercion ----

def _coerce_row_in(raw):
    """Convert a CSV-string row into typed Python values."""
    row = dict(raw)
    for col in _INT_COLS:
        if col in _INT_OR_BLANK_COLS:
            continue
        v = row.get(col, '')
        try:
            row[col] = int(v) if str(v) != '' else 0
        except (TypeError, ValueError):
            row[col] = 0
    for col in _INT_OR_BLANK_COLS:
        v = row.get(col, '')
        if v in ('', None):
            row[col] = ''
        else:
            try:
                row[col] = int(v)
            except (TypeError, ValueError):
                row[col] = ''
    for col in _JSON_COLS:
        v = row.get(col, '')
        if isinstance(v, str) and v.strip():
            try:
                row[col] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                row[col] = None
        elif not isinstance(v, (list, dict)):
            row[col] = None
    if row.get('mode', '') not in VALID_MODES:
        row['mode'] = ''
    if row.get('status', '') not in VALID_STATUSES:
        row['status'] = 'pending'
    return row


def _coerce_row_out(row):
    """Stringify a typed row for csv.DictWriter."""
    out = {}
    for col in TAVERN_CSV_COLUMNS:
        v = row.get(col, '')
        if v is None:
            out[col] = ''
        elif col in _JSON_COLS and isinstance(v, (list, dict)):
            out[col] = json.dumps(v)
        else:
            out[col] = str(v)
    return out


# ---- CRUD on the saved task ----

def _csv_load(session):
    """Read all saved rows. Returns an empty list if no file."""
    with _csv_lock(session):
        try:
            with open(_tavern_csv_path(session), 'r',
                      newline='', encoding='utf-8') as f:
                return [_coerce_row_in(r) for r in csv.DictReader(f)]
        except FileNotFoundError:
            return []


def _csv_save_all(session, rows):
    """Atomically overwrite the CSV with `rows`."""
    path = _tavern_csv_path(session)
    tmp = path + '.tmp'
    with _csv_lock(session):
        with open(tmp, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=TAVERN_CSV_COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow(_coerce_row_out(r))
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)


def _csv_delete_all(session):
    """Wipe the saved task entirely."""
    try:
        os.remove(_tavern_csv_path(session))
    except FileNotFoundError:
        pass


def _csv_update_one(session, city_id, **fields):
    """Update fields on the row for a given city_id (no-op if not found)."""
    rows = _csv_load(session)
    for r in rows:
        if str(r.get('city_id', '')) == str(city_id):
            r.update(fields)
            break
    _csv_save_all(session, rows)


# ---- Worker lifecycle: is a run active? has stop been requested? ----

def _is_worker_running(session):
    """A live worker holds the worker-lock file. The lock is given a stale
    horizon long enough to survive a normal sleep cycle but short enough
    that a crashed worker doesn't leave the menu wedged forever."""
    path = _tavern_worker_lock_path(session)
    if not os.path.exists(path):
        return False
    try:
        # Stale if older than 25h — longer than the max user-set interval.
        age = time.time() - os.path.getmtime(path)
        if age > 25 * 3600:
            return False
    except OSError:
        return False
    return True


def _worker_lock_touch(session):
    """Refresh the worker lock's mtime so _is_worker_running keeps
    returning True. Called every cycle."""
    path = _tavern_worker_lock_path(session)
    try:
        pathlib.Path(path).touch()
    except OSError:
        pass


def _worker_lock_acquire(session):
    """Try to claim the worker lock. Returns True if we got it."""
    return _lock_acquire(_tavern_worker_lock_path(session),
                         timeout=2, stale_after=25 * 3600)


def _worker_lock_release(session):
    _lock_release(_tavern_worker_lock_path(session))


def _signal_stop(session):
    pathlib.Path(_tavern_stop_flag_path(session)).touch()


def _stop_requested(session):
    return os.path.exists(_tavern_stop_flag_path(session))


def _clear_stop_signal(session):
    try:
        os.remove(_tavern_stop_flag_path(session))
    except OSError:
        pass


# ---- Status string for the menu banner ----

def _saved_task_summary(rows):
    """One-line plain-English summary of what's saved, or '' if no task."""
    if not rows:
        return ''
    # All rows share the same mode in our world (we always overwrite).
    mode = rows[0].get('mode', '?')
    n = len(rows)
    city_word = 'city' if n == 1 else 'cities'
    if mode == 'set':
        pct = rows[0].get('pct', '')
        return f"set wine to {pct}% in {n} {city_word}"
    if mode == 'auto':
        intervals = {r.get('interval_hours', 0) for r in rows}
        if len(intervals) == 1:
            iv = next(iter(intervals))
            sched = 'run once' if iv == 0 else f"every {iv}h"
        else:
            mn, mx = min(intervals), max(intervals)
            sched = f"per-city schedules ({mn}-{mx}h)"
        return f"auto-tune {n} {city_word}, {sched}"
    return f"{n} {city_word}"


def _status_line(session):
    """Banner string for the main menu."""
    rows = []
    try:
        rows = _csv_load(session)
    except Exception:
        pass
    running = _is_worker_running(session)
    summary = _saved_task_summary(rows)
    if running:
        head = f"{bcolors.GREEN}RUNNING{bcolors.ENDC}"
    elif rows:
        head = f"{bcolors.WARNING}STOPPED{bcolors.ENDC}"
    else:
        head = f"{bcolors.STONE}no saved task{bcolors.ENDC}"
    return f"Status: {head}" + (f"  |  {summary}" if summary else "")


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


def _safe_set_status(session, text):
    """Update ikabot's cosmetic task-list status string, swallowing any
    error. setStatus writes the shared, lock-protected session file;
    that write can time out on the session lock when multiple ikabot
    tasks write concurrently, when a stale .session.lock lingers, or
    when antivirus / cloud-sync briefly holds the file on Windows.
    None of that warrants crashing or alerting — the status string is
    purely informational, so we just skip the update on failure.
    """
    try:
        session.setStatus(text)
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


# ============================================================================
#  SAVED-TASK HELPERS  — building CSV rows from a configured task,
#  hydrating an in-memory cache from a CSV row, asking per-city intervals
# ============================================================================

def _build_rows(mode, cities_to_process, pct=None, interval_hours=0,
                notification_mode=3, per_city_intervals=None):
    """Construct the CSV rows for a freshly configured task.

    Always one row per city. Empire-wide vs per-city is just an internal
    detail of how the directive fields are populated; the on-disk format
    is uniform.

    Parameters:
      mode: 'set' or 'auto'
      cities_to_process: list of city dicts (need id + name)
      pct: 0-100 for 'set' mode, else None
      interval_hours: default schedule, used for any city without an override
      notification_mode: 1/2/3 for 'auto'; ignored otherwise
      per_city_intervals: optional dict {city_id: hours} overriding the default
    """
    now = int(time.time())
    status = 'pending' if mode == 'set' else 'active'
    rows = []
    for city in cities_to_process:
        city_id = str(city.get('id', ''))
        iv = per_city_intervals.get(city_id, interval_hours) if per_city_intervals else interval_hours
        next_run = (now + iv * 3600) if (mode == 'auto' and iv > 0) else ''
        rows.append({
            'city_id': city_id,
            'city_name': _clean_name(city.get('name', '')),
            'mode': mode,
            'pct': pct if mode == 'set' else '',
            'interval_hours': iv,
            'notification_mode': notification_mode if mode == 'auto' else '',
            'status': status,
            'created_at': now,
            'last_run': '',
            'next_run': next_run,
            'total_runs': 0,
            'tavern_pos': '',
            'tavern_level': '',
            'max_wine_level': '',
            'sat_per_wine_json': None,
            'consumption_values_json': None,
            'last_applied_level': '',
            'notes': '',
            'schema_version': TAVERN_SCHEMA_VERSION,
        })
    return rows


def _hydrate_cache_from_rows(mgr, rows):
    """Pre-populate the in-memory caches from saved-row data so the
    worker can skip the city HTML fetch on every cycle. The CSRF
    action_code is session-scoped, so we deliberately do NOT inject
    a cached tavern entry — the first apply will fetch a fresh one."""
    for r in rows:
        city_id = str(r.get('city_id', ''))
        pos = r.get('tavern_pos', '')
        if pos == '' or pos is None:
            continue
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            continue
        try:
            level = int(r.get('tavern_level', 0))
        except (TypeError, ValueError):
            level = 0
        # _get_positions expects {'tavern': <building dict>, 'th_pos': int}.
        # We don't have th_pos in CSV (it's discovered alongside tavern_pos),
        # so leave th_pos None and let the first town-hall lookup raise
        # _StaleCacheError, which forces a fresh _get_positions fetch and
        # populates everything correctly. We only set tavern here so the
        # apply path has a position to use immediately.
        # (If you'd rather avoid that one extra fetch, also persist th_pos —
        # added later.)
        mgr._positions[city_id] = {
            'tavern': {'position': pos, 'level': level, 'building': 'tavern'},
            'th_pos': None,
        }


def _save_task(session, rows):
    """Overwrite the saved task with `rows`. New configurations always
    replace the previous saved task — there is only ever one task on
    disk at a time, by design."""
    _csv_save_all(session, rows)


def _persist_rows(session, rows):
    """Same as _save_task but conveys intent during the scheduler loop."""
    _csv_save_all(session, rows)


def _ask_yes_no(prompt, default_yes=True):
    """Returns True for yes, False for no. Empty input takes the default."""
    yes_label = "[Y/n]" if default_yes else "[y/N]"
    answer = read(msg=f"{prompt} {yes_label}: ",
                  values=["y", "Y", "n", "N", ""])
    if answer == "":
        return default_yes
    return answer.lower() == "y"


def _per_city_interval_prompt(cities_to_process, default_hours):
    """Ask whether selected cities should share one interval or each
    have their own. Returns a dict {city_id: hours} only when the user
    chose per-city; otherwise returns None (callers fall back to the
    shared default_hours).

    Only invoked from the 'pick specific cities' path; empire-wide and
    'all cities' paths skip this and use the shared interval.
    """
    if not cities_to_process:
        return None
    print()
    print("Use the same schedule for every selected city?")
    print()
    print(f"  (1) Yes — every city checks every {default_hours} hour(s)")
    print(f"  (2) No — set a different interval for each city")
    print()
    choice = read(msg="Pick 1 or 2: ", min=1, max=2, digit=True)
    if choice == 1:
        return None

    overrides = {}
    print()
    print("For each city, type the number of hours between checks")
    print("(1-24). Press Enter to use the default of "
          f"{default_hours} hour(s).")
    print()
    for city in cities_to_process:
        name = _clean_name(city.get('name', ''))
        ans = read(
            msg=f"  {name}: ",
            min=1, max=24, digit=True, empty=True,
        )
        if ans == "" or ans is None:
            overrides[str(city['id'])] = default_hours
        else:
            overrides[str(city['id'])] = int(ans)
    return overrides


# ============================================================================
#  SCHEDULER LOOP  — runs the saved task, honouring per-city intervals
#  and the stop signal
# ============================================================================

def _run_scheduler_loop(session, mgr, notification_mode):
    """The continuous loop for an auto-tune task.

    Reads the CSV at the top of each tick, processes whatever cities are
    due, writes their last_run/next_run/total_runs back, then sleeps
    until the earliest next_run. The stop flag is checked between
    sleeps so a deactivate request takes effect within at most 60
    seconds.

    Returns cleanly when the stop flag is set or when no rows remain
    active (e.g. a one-shot set-mode task finished).
    """
    info = f"\nWine auto-tune: CSV-driven scheduler\n"
    try:
        setInfoSignal(session, info)
    except Exception:
        traceback.print_exc()

    # Make sure we don't start with a leftover stop flag.
    _clear_stop_signal(session)

    while True:
        if _stop_requested(session):
            print("Stop signal received — exiting auto-tune cleanly.")
            break

        try:
            rows = _csv_load(session)
        except Exception:
            traceback.print_exc()
            rows = []

        active_rows = [r for r in rows if r.get('status') == 'active']
        if not active_rows:
            print("No active cities left in the saved task — exiting.")
            break

        now = int(time.time())
        due = [r for r in active_rows if _is_due(r, now)]

        if due:
            cities = {
                str(r['city_id']): {
                    'id': str(r['city_id']),
                    'name': r.get('city_name', ''),
                }
                for r in due
            }
            cities_ids = [str(r['city_id']) for r in due]

            try:
                results = mgr.process_equilibrium(cities_ids, cities)
                _print_results_table(results)
                _merge_results_into_rows(rows, results)
            except KeyboardInterrupt:
                raise
            except Exception:
                cycle_err = (
                    "Wine auto-tune had a problem this cycle, but it will keep running.\n"
                    f"Details:\n{traceback.format_exc()}"
                )
                traceback.print_exc()
                if notification_mode in (1, 2):
                    try:
                        sendToBot(session, cycle_err)
                    except Exception:
                        traceback.print_exc()

            # Update lifecycle fields for everything we processed.
            for r in due:
                r['last_run'] = now
                iv = r.get('interval_hours', 0) or 0
                r['total_runs'] = (r.get('total_runs') or 0) + 1
                if iv > 0:
                    r['next_run'] = now + iv * 3600
                else:
                    # One-shot row inside an auto-tune task — mark done.
                    r['next_run'] = ''
                    r['status'] = 'completed'

            # Persist the merged set of rows.
            try:
                _persist_rows(session, rows)
            except Exception:
                traceback.print_exc()

            _safe_set_status(session, f"Wine auto-tune ran at {getDateTime()}")
            _worker_lock_touch(session)

        # Sleep until the earliest upcoming next_run (or 1 minute if
        # nothing is scheduled), checking the stop flag every minute.
        upcoming = [
            r.get('next_run') for r in rows
            if r.get('status') == 'active' and isinstance(r.get('next_run'), int)
        ]
        if not upcoming:
            # No active rows left — let the next loop iteration exit.
            time.sleep(60)
            continue

        sleep_until = min(upcoming)
        while True:
            if _stop_requested(session):
                break
            remaining = sleep_until - int(time.time())
            if remaining <= 0:
                break
            time.sleep(min(60, remaining))
            _worker_lock_touch(session)


def _is_due(row, now):
    """A row is due if next_run is blank (never scheduled — first run)
    or its scheduled time has passed."""
    nr = row.get('next_run', '')
    if nr == '' or nr is None:
        return True
    try:
        return int(nr) <= now
    except (TypeError, ValueError):
        return True


def _merge_results_into_rows(all_rows, results):
    """After a cycle, write back the cached static data the manager
    just discovered (consumption table, sat-per-wine, current level)
    so the next process restart can skip those fetches."""
    by_name = {r.get('name', ''): r for r in results}
    for r in all_rows:
        # Result rows are matched by city name (since that's what the
        # results table carries). Fall back to last_applied_level being
        # updated by the manager directly when we have a stronger key.
        name = r.get('city_name', '')
        if name not in by_name:
            continue
        # results don't currently include the wine level explicitly
        # in the dict; nothing else to copy here. The manager-side
        # cache update (after a successful apply) writes
        # last_applied_level through the persistence wrapper instead.




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
        print(f"  {_status_line(session)}")
        print()
        print("Your tavern serves wine to keep citizens happy. More wine")
        print("= more happiness = faster population growth, but it also")
        print("burns through your wine stockpile.")
        print()

        # State-aware menu: options appear only when they're applicable,
        # so the user can't, say, Delete while running or Stop with
        # nothing to stop.
        rows = []
        try:
            rows = _csv_load(session)
        except Exception:
            traceback.print_exc()
        has_saved = bool(rows)
        running = _is_worker_running(session)

        # Build the menu dynamically. choice_actions maps the printed
        # number to a callable.
        choice_actions = {}
        idx = 1

        if has_saved and not running:
            print(f"  ({idx}) Resume the saved task")
            print(f"      Re-run the saved configuration. Reuses cached")
            print(f"      info so it's faster than starting from scratch.")
            print()
            choice_actions[idx] = ('resume', None)
            idx += 1

        if running:
            print(f"  ({idx}) Stop the running task")
            print(f"      Signals the background scheduler to exit cleanly.")
            print(f"      Saved state is preserved — Resume later.")
            print()
            choice_actions[idx] = ('stop', None)
            idx += 1

        if has_saved:
            print(f"  ({idx}) View the saved task")
            print(f"      See per-city directives, schedules, run history.")
            print()
            choice_actions[idx] = ('view', None)
            idx += 1

        if has_saved and not running:
            print(f"  ({idx}) Delete the saved task")
            print(f"      Wipe the CSV. Cannot be undone.")
            print()
            choice_actions[idx] = ('delete', None)
            idx += 1

        if not running:
            print(f"  ({idx}) Set a wine level by hand")
            print(f"      Apply a fixed percentage (0-100%) to chosen cities.")
            print(f"      {bcolors.WARNING}Overwrites any saved task.{bcolors.ENDC}")
            print()
            choice_actions[idx] = ('set', None)
            idx += 1

            print(f"  ({idx}) Auto-tune wine (runs in the background)")
            print(f"      Sets MAX wine while cities are still growing, then")
            print(f"      drops wine to the lowest level that keeps them happy")
            print(f"      once they hit max population.")
            print(f"      {bcolors.WARNING}Overwrites any saved task.{bcolors.ENDC}")
            print()
            choice_actions[idx] = ('auto', None)
            idx += 1

        print("  (') Back to the main ikabot menu")
        print()

        max_choice = max(choice_actions) if choice_actions else 1
        choice = read(
            msg=f"Pick 1-{max_choice}: ",
            min=1, max=max_choice, digit=True,
            additionalValues=["'"],
        )

        if choice == "'":
            event.set()
            return

        print()

        action = choice_actions.get(choice, ('noop', None))[0]
        if action == 'resume':
            _run_resume_task(session, event, stdin_fd, predetermined_input)
        elif action == 'stop':
            _run_stop_task(session)
        elif action == 'view':
            _run_view_task(session)
        elif action == 'delete':
            _run_delete_task(session)
        elif action == 'set':
            _run_set_mode(session)
        elif action == 'auto':
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
    print("(This will overwrite any saved task.)")
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
    if _csv_load(session):
        print(f"{bcolors.WARNING}This will replace your current saved task.{bcolors.ENDC}")
    print()
    if not _ask_yes_no("Go ahead?"):
        print("Cancelled, nothing was changed.")
        print()
        enter()
        return

    # Save the task before doing any work, so a Resume after a crash
    # still has the directive.
    rows = _build_rows(
        mode='set',
        cities_to_process=cities_to_process,
        pct=pct,
        interval_hours=0,
    )
    _save_task(session, rows)

    print()
    print("Working through your cities...")
    print()

    mgr = TavernManager(session)
    success_count = sum(1 for city in cities_to_process if mgr.set_tavern_pct(city, pct))

    # Update lifecycle now that the cycle is done.
    now = int(time.time())
    for r in rows:
        r['last_run'] = now
        r['total_runs'] = 1
        r['status'] = 'completed'
    try:
        _persist_rows(session, rows)
    except Exception:
        traceback.print_exc()

    print()
    print(f"{bcolors.GREEN}Done.{bcolors.ENDC}")
    print(f"{success_count} of {len(cities_to_process)} cities updated successfully.")
    if success_count < len(cities_to_process):
        print("(Cities that failed are listed above with the reason.)")
    print()
    print("Saved — pick Resume from the main menu to re-apply this same")
    print("configuration later.")
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
    """Configure an auto-tune task: collects all the choices, saves them
    to the CSV (immediately on confirm — by design), and hands off to
    the scheduler loop."""
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
    print("Four quick questions: Telegram alerts, which cities, schedule,")
    print("and (only if you pick specific cities) per-city schedule.")
    print()
    print("-" * 60)
    print("Question 1: Telegram alerts")
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
    print("Question 2: Which cities")
    print("-" * 60)
    print()
    print("Which cities should auto-tune?")
    print()
    print("  (1) Choose a subset")
    print("      Start with every city, then remove the ones you don't")
    print("      want auto-tuned. (This is the only path that lets you")
    print("      set per-city schedules later.)")
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
        is_subset = True
    else:
        cities_ids, cities = getIdsOfCities(session)
        is_subset = False

    if not cities_ids:
        print("No cities selected — nothing to do.")
        enter()
        return

    cities_to_process = [cities[cid] for cid in cities_ids]

    print()
    print("-" * 60)
    print("Question 3: Schedule")
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

    # Question 4 (only when the user picked specific cities AND has more
    # than one of them AND is on a real schedule): per-city intervals.
    per_city_intervals = None
    if is_subset and len(cities_to_process) > 1 and run_hours > 0:
        print()
        print("-" * 60)
        print("Question 4: Per-city schedule (optional)")
        print("-" * 60)
        per_city_intervals = _per_city_interval_prompt(cities_to_process, run_hours)

    print()
    if per_city_intervals:
        unique = set(per_city_intervals.values())
        if len(unique) == 1:
            sched_summary = f"every {next(iter(unique))} hour(s) (per-city schedule, all the same)"
        else:
            sched_summary = (
                f"per-city schedule ({min(unique)}-{max(unique)} hours apart)"
            )
    else:
        sched_summary = (
            "one check then exit" if run_hours == 0
            else f"every {run_hours} hour(s)"
        )
    city_word = "city" if len(cities_to_process) == 1 else "cities"
    print(f"About to start auto-tune for {len(cities_to_process)} {city_word}, {sched_summary}.")
    if _csv_load(session):
        print(f"{bcolors.WARNING}This will replace your current saved task.{bcolors.ENDC}")
    print()
    if not _ask_yes_no("Go ahead?"):
        print("Cancelled, no task was saved.")
        enter()
        return

    # Save immediately on confirm (by design — if the first run dies,
    # the config is still on disk and Resume will work).
    rows = _build_rows(
        mode='auto',
        cities_to_process=cities_to_process,
        interval_hours=run_hours,
        notification_mode=notification_mode,
        per_city_intervals=per_city_intervals,
    )
    _save_task(session, rows)
    print(f"{bcolors.GREEN}Saved.{bcolors.ENDC}")
    print()

    mgr = TavernManager(session, notification_mode)
    _hydrate_cache_from_rows(mgr, rows)

    # Show the result of the first cycle in the foreground.
    print("Running the first check now...")
    print()
    try:
        results = mgr.process_equilibrium(cities_ids, cities)
        _print_results_table(results)
    except Exception:
        traceback.print_exc()

    # Update lifecycle for the first cycle so Resume picks up the correct
    # next_run if the user later restarts mid-schedule.
    now = int(time.time())
    for r in rows:
        r['last_run'] = now
        r['total_runs'] = 1
        iv = r.get('interval_hours', 0) or 0
        if iv > 0:
            r['next_run'] = now + iv * 3600
        else:
            r['next_run'] = ''
            r['status'] = 'completed'
    try:
        _persist_rows(session, rows)
    except Exception:
        traceback.print_exc()

    if run_hours == 0:
        print()
        print("Done. Use option (1) Resume to re-run this same configuration later.")
        enter()
        return

    enter()

    set_child_mode(session)
    event.set()

    if not _worker_lock_acquire(session):
        # Shouldn't happen since the menu blocks starts when running.
        return

    try:
        _run_scheduler_loop(session, mgr, notification_mode)
    finally:
        _worker_lock_release(session)
        _clear_stop_signal(session)
        session.logout()


# ============================================================================
#  RESUME / VIEW / DELETE  — menu actions on a saved task
# ============================================================================

def _run_resume_task(session, event, stdin_fd, predetermined_input):
    """Re-run the saved task. For 'set' mode, re-applies the saved
    percentage once. For 'auto' mode, hands off to the scheduler loop
    (which is what makes the daemon survive restarts)."""
    rows = _csv_load(session)
    if not rows:
        print("No saved task to resume.")
        print()
        enter()
        return

    if _is_worker_running(session):
        print("A task is already running. Stop it first to resume.")
        print()
        enter()
        return

    mode = rows[0].get('mode', 'auto')
    notification_mode = rows[0].get('notification_mode', 3) or 3

    print()
    print(f"About to resume: {_saved_task_summary(rows)}")
    print()
    if not _ask_yes_no("Go ahead?"):
        print("Cancelled.")
        enter()
        return

    print()

    # Build the cities-dict shape the existing methods expect.
    cities = {
        str(r['city_id']): {
            'id': str(r['city_id']),
            'name': r.get('city_name', ''),
        }
        for r in rows
    }
    cities_ids = [str(r['city_id']) for r in rows]

    mgr = TavernManager(session, notification_mode)
    _hydrate_cache_from_rows(mgr, rows)

    if mode == 'set':
        # One-shot — just re-apply the saved percentage to every city.
        pct = rows[0].get('pct', 0) or 0
        print(f"Re-applying {pct}% to {len(rows)} city/cities...")
        print()
        ok_count = 0
        for r in rows:
            city = cities[str(r['city_id'])]
            if mgr.set_tavern_pct(city, pct):
                ok_count += 1
        # Update lifecycle.
        now = int(time.time())
        for r in rows:
            r['last_run'] = now
            r['total_runs'] = (r.get('total_runs') or 0) + 1
            r['status'] = 'completed'
        try:
            _persist_rows(session, rows)
        except Exception:
            traceback.print_exc()
        print()
        print(f"{bcolors.GREEN}Done.{bcolors.ENDC}  {ok_count} of {len(rows)} updated.")
        print()
        enter()
        return

    # Auto-tune resume: foreground first cycle for any rows that are
    # already due, then go background and let the scheduler take over.
    now = int(time.time())
    due_rows = [r for r in rows if _is_due(r, now)]
    if due_rows:
        due_cities = {str(r['city_id']): cities[str(r['city_id'])] for r in due_rows}
        due_ids = [str(r['city_id']) for r in due_rows]
        print(f"{len(due_rows)} city/cities are due now — running them in the foreground...")
        print()
        try:
            results = mgr.process_equilibrium(due_ids, due_cities)
            _print_results_table(results)
        except Exception:
            traceback.print_exc()
        for r in due_rows:
            r['last_run'] = now
            iv = r.get('interval_hours', 0) or 0
            r['total_runs'] = (r.get('total_runs') or 0) + 1
            if iv > 0:
                r['next_run'] = now + iv * 3600
            else:
                r['next_run'] = ''
                r['status'] = 'completed'
        try:
            _persist_rows(session, rows)
        except Exception:
            traceback.print_exc()
        enter()
    else:
        print("Nothing is due yet — handing off to the background scheduler.")
        print()
        enter()

    set_child_mode(session)
    event.set()

    if not _worker_lock_acquire(session):
        return
    try:
        _run_scheduler_loop(session, mgr, notification_mode)
    finally:
        _worker_lock_release(session)
        _clear_stop_signal(session)
        session.logout()


def _run_stop_task(session):
    """Touch the stop flag so the scheduler loop exits cleanly within
    a minute or two. Saved state is preserved."""
    if not _is_worker_running(session):
        print("No task is currently running.")
        print()
        enter()
        return
    _signal_stop(session)
    print(f"{bcolors.GREEN}Stop signal sent.{bcolors.ENDC}")
    print("The running task will exit cleanly within ~1 minute after its")
    print("current cycle (if any) finishes. Saved state is kept — you can")
    print("Resume later.")
    print()
    enter()


def _run_view_task(session):
    """Show the saved task as a table: one row per city with the
    configured directive, the next scheduled run, and any cached
    static data."""
    rows = _csv_load(session)
    if not rows:
        print("No saved task.")
        print()
        enter()
        return

    print()
    print(f"{bcolors.BLUE}{_saved_task_summary(rows)}{bcolors.ENDC}")
    print()

    col_name = max((len(r.get('city_name', '')) for r in rows), default=8)
    col_name = max(col_name, 6)

    header = (
        f"{'City':<{col_name}}  {'Mode':<5}  {'Cfg':<7}  "
        f"{'Status':<10}  {'Runs':>4}  {'Last run':<16}  {'Next run':<16}"
    )
    divider = "─" * len(header)
    print(header)
    print(divider)
    for r in rows:
        name = r.get('city_name', '')
        mode = r.get('mode', '')
        if mode == 'set':
            cfg = f"{r.get('pct', '')}%"
        else:
            iv = r.get('interval_hours', 0)
            cfg = f"{iv}h" if iv else "once"
        status = r.get('status', '')
        runs = r.get('total_runs', 0) or 0
        last_run = r.get('last_run', '')
        if isinstance(last_run, int) and last_run:
            try:
                last_str = getDateTime(last_run)[5:16]
            except Exception:
                last_str = ""
        else:
            last_str = "never"
        next_run = r.get('next_run', '')
        if isinstance(next_run, int) and next_run:
            try:
                next_str = getDateTime(next_run)[5:16]
            except Exception:
                next_str = ""
        elif r.get('status') == 'completed':
            next_str = "(done)"
        else:
            next_str = "asap"
        print(f"{name:<{col_name}}  {mode:<5}  {cfg:<7}  {status:<10}  {runs:>4}  {last_str:<16}  {next_str:<16}")
    print(divider)
    print()
    print(f"CSV: {_tavern_csv_path(session)}")
    print()
    enter()


def _run_delete_task(session):
    """Wipe the saved task. Requires confirmation. Will not run while
    a worker is active — stop it first."""
    if _is_worker_running(session):
        print("A task is currently running. Stop it before deleting.")
        print()
        enter()
        return
    rows = _csv_load(session)
    if not rows:
        print("No saved task to delete.")
        print()
        enter()
        return
    print()
    print(f"About to delete: {_saved_task_summary(rows)}")
    print()
    print(f"{bcolors.WARNING}This cannot be undone.{bcolors.ENDC}")
    print()
    if not _ask_yes_no("Really delete?", default_yes=False):
        print("Cancelled.")
        enter()
        return
    _csv_delete_all(session)
    print(f"{bcolors.GREEN}Saved task deleted.{bcolors.ENDC}")
    print()
    enter()
