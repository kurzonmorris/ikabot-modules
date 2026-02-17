#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import json
import time
import re
import gzip
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import *
from ikabot.helpers.getJson import getCity, getIsland

MODULE_VERSION = "v1.3"
MODULE_NAME = "Spy Tool"
DEBUG_ENABLED = True
DEBUG_MAX_AGE_DAYS = 2
DEBUG_MAX_SIZE_MB = 10
LOCK_TIMEOUT_SECONDS = 15

_debug_log_path = None
_storage_path = None
_lock_file = None


class FileLock:
    """Simple file lock with timeout"""
    
    def __init__(self, lock_path, timeout=LOCK_TIMEOUT_SECONDS):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.locked = False
    
    def acquire(self):
        """Acquire lock atomically, wait up to timeout seconds."""
        start_time = time.time()

        while time.time() - start_time < self.timeout:
            try:
                if self.lock_path.exists():
                    lock_age = time.time() - self.lock_path.stat().st_mtime
                    if lock_age > self.timeout:
                        self.lock_path.unlink()
                        debug_log(f"Removed stale lock file (age: {lock_age:.1f}s)")
                    else:
                        time.sleep(0.5)
                        continue

                with open(self.lock_path, "x", encoding="utf-8") as lock_file:
                    lock_file.write(f"{os.getpid()}\n{time.time()}")

                self.locked = True
                debug_log(f"Lock acquired: {self.lock_path}")
                return True

            except FileExistsError:
                time.sleep(0.5)
            except Exception as e:
                debug_log_error("Lock acquire error", e)
                time.sleep(0.5)

        debug_log(f"Lock timeout after {self.timeout}s: {self.lock_path}")
        return False
    
    def release(self):
        """Release the lock"""
        try:
            if self.locked and self.lock_path.exists():
                self.lock_path.unlink()
                self.locked = False
                debug_log(f"Lock released: {self.lock_path}")
                return True
        except Exception as e:
            debug_log_error(f"Lock release error", e)
        return False
    
    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock: {self.lock_path}")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


def get_desktop_path():
    """Get desktop path cross-platform"""
    if sys.platform == "win32":
        return Path(os.path.join(os.environ["USERPROFILE"], "Desktop"))
    elif sys.platform == "darwin":
        return Path(os.path.expanduser("~/Desktop"))
    else:
        return Path(os.path.expanduser("~/Desktop"))


def get_debug_folder():
    """Get/create debug folder: Desktop/ikabot_debug/spyTool/"""
    desktop = get_desktop_path()
    debug_folder = desktop / "ikabot_debug" / "spyTool"
    debug_folder.mkdir(parents=True, exist_ok=True)
    return debug_folder


def get_current_log_filename():
    """Get log filename for current 15-min window"""
    now = datetime.now()
    minute_block = (now.minute // 15) * 15
    timestamp = now.replace(minute=minute_block, second=0, microsecond=0)
    return f"spytool_{timestamp.strftime('%Y-%m-%d_%H-%M')}.log"


def cleanup_old_logs(debug_folder):
    """Remove logs older than 2 days or if total size > 10MB"""
    try:
        log_files = sorted(debug_folder.glob("spytool_*.log"), key=lambda f: f.stat().st_mtime)
        cutoff_time = datetime.now() - timedelta(days=DEBUG_MAX_AGE_DAYS)
        total_size = 0
        
        for log_file in log_files:
            file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
            file_size = log_file.stat().st_size
            
            if file_time < cutoff_time:
                log_file.unlink()
                continue
            
            total_size += file_size
        
        if total_size > DEBUG_MAX_SIZE_MB * 1024 * 1024:
            log_files = sorted(debug_folder.glob("spytool_*.log"), key=lambda f: f.stat().st_mtime)
            while total_size > DEBUG_MAX_SIZE_MB * 1024 * 1024 and len(log_files) > 1:
                oldest = log_files.pop(0)
                total_size -= oldest.stat().st_size
                oldest.unlink()
    except Exception as e:
        pass


def debug_log(message, level="INFO"):
    """Write timestamped message to debug log"""
    global _debug_log_path, DEBUG_ENABLED
    
    if not DEBUG_ENABLED:
        return
    
    try:
        debug_folder = get_debug_folder()
        cleanup_old_logs(debug_folder)
        
        log_filename = get_current_log_filename()
        log_path = debug_folder / log_filename
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        log_entry = f"[{timestamp}] [{level}] {message}\n"
        
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
        
        _debug_log_path = log_path
        
    except Exception as e:
        pass


def debug_log_error(message, exception=None):
    """Log error with optional traceback"""
    if exception:
        tb = traceback.format_exc()
        debug_log(f"{message}\nException: {exception}\nTraceback:\n{tb}", "ERROR")
    else:
        debug_log(message, "ERROR")


def debug_log_response(function_name, response_snippet):
    """Log API response (truncated for readability)"""
    snippet = str(response_snippet)[:500] + "..." if len(str(response_snippet)) > 500 else str(response_snippet)
    debug_log(f"[{function_name}] Response: {snippet}", "DEBUG")


def get_ikabot_install_path():
    """Detect ikabot installation path"""
    try:
        import ikabot
        ikabot_path = Path(ikabot.__file__).parent
        debug_log(f"Detected ikabot path: {ikabot_path}")
        return ikabot_path
    except Exception as e:
        debug_log_error("Could not detect ikabot path", e)
        return None


def get_default_storage_path():
    """Get default storage path: {ikabot_install}/spy_data/"""
    ikabot_path = get_ikabot_install_path()
    if ikabot_path:
        storage_path = ikabot_path / "spy_data"
        return storage_path
    return None


def test_storage_location(path):
    """Test if location is writable by creating and removing a test file"""
    try:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        
        test_file = path / ".spytool_write_test"
        test_file.write_text("test")
        
        if test_file.exists():
            test_file.unlink()
            debug_log(f"Storage location verified: {path}")
            return True
        return False
    except Exception as e:
        debug_log_error(f"Storage location test failed: {path}", e)
        return False


def initialize_storage(custom_path=None):
    """Initialize storage location, returns path or None on failure"""
    global _storage_path
    
    if custom_path:
        path = Path(custom_path)
    else:
        path = get_default_storage_path()
    
    if path and test_storage_location(path):
        _storage_path = path
        
        lock_path = path / "spy_data.lock"
        players_json = path / "players_intel.json"
        players_html = path / "players_intel.html"
        
        if not players_json.exists():
            try:
                players_json.write_text("{}")
                debug_log(f"Created empty players_intel.json")
            except Exception as e:
                debug_log_error("Failed to create players_intel.json", e)
        
        if not players_html.exists():
            try:
                html_template = """<!DOCTYPE html>
<html>
<head>
    <title>Spy Tool - Player Intelligence</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        h1 { color: #333; }
        .no-data { color: #666; font-style: italic; }
    </style>
</head>
<body>
    <h1>Spy Tool - Player Intelligence</h1>
    <p class="no-data">No player data recorded yet.</p>
</body>
</html>"""
                players_html.write_text(html_template)
                debug_log(f"Created empty players_intel.html")
            except Exception as e:
                debug_log_error("Failed to create players_intel.html", e)
        
        debug_log(f"Storage initialized: {_storage_path}")
        return _storage_path
    
    debug_log_error(f"Failed to initialize storage at: {path}")
    return None


def get_lock():
    """Get a lock object for the storage location"""
    global _storage_path
    if _storage_path:
        lock_path = _storage_path / "spy_data.lock"
        return FileLock(lock_path)
    return None


def search_player_by_name(session, player_name):
    """
    Search for a player using the highscore search
    Returns player info including their ID if found
    
    Parameters
    ----------
    session : ikabot.web.session.Session
    player_name : str
    
    Returns
    -------
    player_info : dict or None
        Contains 'id', 'name', 'score', 'rank' if found
    """
    debug_log(f"Searching for player: {player_name}")
    
    try:
        html = session.get()
        city_id = re.search(r"currentCityId:\s(\d+),", html).group(1)
        
        url = f"view=highscore&searchUser={player_name}&currentCityId={city_id}&actionRequest={actionRequest}&ajax=1"
        response = session.post(url)
        debug_log_response("search_player_by_name", response[:2000] if len(response) > 2000 else response)
        
        data = json.loads(response, strict=False)
        
        for item in data:
            if isinstance(item, list) and item[0] == "changeView":
                html_content = item[1][1] if len(item[1]) > 1 else ""
                
                rows = re.findall(
                    r'<tr[^>]*class="[^"]*result[^"]*"[^>]*>.*?</tr>',
                    html_content,
                    re.DOTALL
                )
                
                for row in rows:
                    rank_match = re.search(r'<td[^>]*>\s*(\d+)\.\s*</td>', row)
                    name_match = re.search(r"'playerId':\s*(\d+)[^>]*>([^<]+)</a>", row)
                    score_match = re.search(r'<td[^>]*class="score"[^>]*>([0-9,]+)</td>', row)
                    
                    if name_match:
                        found_id = name_match.group(1)
                        found_name = name_match.group(2).strip()
                        
                        if found_name.lower() == player_name.lower():
                            result = {
                                "id": found_id,
                                "name": found_name,
                                "score": int(score_match.group(1).replace(",", "")) if score_match else 0,
                                "rank": int(rank_match.group(1)) if rank_match else 0
                            }
                            debug_log(f"Found exact match: {result}")
                            return result
                
                all_players = re.findall(
                    r"'playerId':\s*(\d+)[^>]*>([^<]+)</a>",
                    html_content
                )
                
                for pid, pname in all_players:
                    if pname.strip().lower() == player_name.lower():
                        debug_log(f"Found match: {pname.strip()} (ID: {pid})")
                        return {"id": pid, "name": pname.strip(), "score": 0, "rank": 0}
        
        debug_log(f"Player not found: {player_name}")
        return None
        
    except Exception as e:
        debug_log_error(f"Error searching for player: {player_name}", e)
        return None


def get_all_player_scores(session, player_id, player_name):
    """
    Get all score categories for a player from highscore
    
    Parameters
    ----------
    session : ikabot.web.session.Session
    player_id : str
    player_name : str
    
    Returns
    -------
    scores : dict with all score categories
    """
    debug_log(f"Getting all scores for player: {player_name} (ID: {player_id})")
    
    scores = {
        "total": {"score": 0, "rank": 0},
        "building": {"score": 0, "rank": 0},
        "research": {"score": 0, "rank": 0},
        "army": {"score": 0, "rank": 0},
        "trading": {"score": 0, "rank": 0},
        "gold": {"score": 0, "rank": 0},
    }
    
    score_types = [
        ("total", ""),
        ("building", "building"),
        ("research", "research"),
        ("army", "army"),
        ("trading", "trader"),
        ("gold", "gold"),
    ]
    
    try:
        html = session.get()
        city_id = re.search(r"currentCityId:\s(\d+),", html).group(1)
        
        for score_name, score_type in score_types:
            try:
                url = f"view=highscore&searchUser={player_name}&scoreType={score_type}&currentCityId={city_id}&actionRequest={actionRequest}&ajax=1"
                response = session.post(url)
                
                data = json.loads(response, strict=False)
                
                for item in data:
                    if isinstance(item, list) and item[0] == "changeView":
                        html_content = item[1][1] if len(item[1]) > 1 else ""
                        
                        rows = re.findall(
                            r'<tr[^>]*class="[^"]*result[^"]*"[^>]*>.*?</tr>',
                            html_content,
                            re.DOTALL
                        )
                        
                        for row in rows:
                            if f"'playerId':{player_id}" in row.replace(" ", "") or f"'playerId': {player_id}" in row:
                                rank_match = re.search(r'<td[^>]*>\s*(\d+)\.\s*</td>', row)
                                score_match = re.search(r'<td[^>]*class="score"[^>]*>([0-9,]+)</td>', row)
                                
                                if score_match:
                                    scores[score_name]["score"] = int(score_match.group(1).replace(",", ""))
                                if rank_match:
                                    scores[score_name]["rank"] = int(rank_match.group(1))
                                break
                
                time.sleep(0.2)
                
            except Exception as e:
                debug_log_error(f"Error getting {score_name} score", e)
        
        debug_log(f"Scores retrieved: {scores}")
        return scores
        
    except Exception as e:
        debug_log_error(f"Error getting scores for player: {player_name}", e)
        return scores


def get_all_islands_with_players(session):
    """
    Get all islands on the map that have at least one player
    Uses WorldMap API similar to dumpWorld.py
    
    Returns
    -------
    islands : list of dict
        Each dict has: id, x, y, name, players (count)
    """
    debug_log("Getting all islands with players from world map...")
    
    islands = []
    
    try:
        quadrants = [
            (0, 50, 0, 50),
            (50, 100, 0, 50),
            (0, 50, 50, 100),
            (50, 100, 50, 100)
        ]
        
        for x_min, x_max, y_min, y_max in quadrants:
            url = f"action=WorldMap&function=getJSONArea&x_min={x_min}&x_max={x_max}&y_min={y_min}&y_max={y_max}"
            response = session.post(url)
            data = json.loads(response)
            
            for x, y_data in data.get("data", {}).items():
                for y, island_data in y_data.items():
                    player_count = int(island_data[7]) if len(island_data) > 7 else 0
                    if player_count > 0:
                        islands.append({
                            "id": island_data[0],
                            "x": int(x),
                            "y": int(y),
                            "name": island_data[1],
                            "resource_type": island_data[2],
                            "miracle_type": island_data[3],
                            "wood_level": island_data[6] if len(island_data) > 6 else "0",
                            "players": player_count
                        })
            
            time.sleep(0.3)
        
        debug_log(f"Found {len(islands)} islands with players")
        return islands
        
    except Exception as e:
        debug_log_error("Error getting islands from world map", e)
        return []


def get_server_cache_path():
    """Get path to server cache file"""
    storage = get_storage_path()
    if storage:
        return storage / "server_cache.json.gz"
    return None


def load_server_cache():
    """Load server cache from disk"""
    cache_path = get_server_cache_path()
    if not cache_path or not cache_path.exists():
        return None
    
    try:
        import gzip
        with gzip.open(cache_path, "rt", encoding="utf-8") as f:
            cache = json.load(f)
        debug_log(f"Loaded server cache: {len(cache.get('islands', []))} islands, {cache.get('total_players', 0)} players")
        return cache
    except Exception as e:
        debug_log_error("Error loading server cache", e)
        return None


def save_server_cache(cache):
    """Save server cache to disk (gzipped)"""
    cache_path = get_server_cache_path()
    if not cache_path:
        return False
    
    try:
        import gzip
        with gzip.open(cache_path, "wt", encoding="utf-8") as f:
            json.dump(cache, f)
        debug_log(f"Saved server cache: {len(cache.get('islands', []))} islands")
        return True
    except Exception as e:
        debug_log_error("Error saving server cache", e)
        return False


def get_cache_age_str(cache):
    """Get human-readable age of cache"""
    if not cache or "timestamp" not in cache:
        return "Unknown"
    
    try:
        cache_time = datetime.fromisoformat(cache["timestamp"])
        age = datetime.now() - cache_time
        
        if age.days > 0:
            return f"{age.days} days ago"
        elif age.seconds >= 3600:
            return f"{age.seconds // 3600} hours ago"
        elif age.seconds >= 60:
            return f"{age.seconds // 60} minutes ago"
        else:
            return "Just now"
    except:
        return "Unknown"


def build_server_cache(session, progress_callback=None, request_delay=1.5):
    """
    Build a full server cache by scanning all islands
    
    Parameters
    ----------
    session : ikabot.web.session.Session
    progress_callback : callable, optional
        Called with (current, total, message)
    request_delay : float
        Delay between requests in seconds (default 1.5s to avoid rate limiting)
    
    Returns
    -------
    cache : dict
    """
    debug_log(f"Building server cache with {request_delay}s delay...")
    
    cache = {
        "server": f"s{session.mundo}-{session.servidor}" if hasattr(session, 'mundo') else "unknown",
        "timestamp": datetime.now().isoformat(),
        "islands": [],
        "players": {},
        "total_players": 0
    }
    
    islands_shallow = get_all_islands_with_players(session)
    
    if not islands_shallow:
        debug_log("No islands found")
        return cache
    
    total = len(islands_shallow)
    debug_log(f"Scanning {total} islands...")
    
    for i, island_info in enumerate(islands_shallow):
        if progress_callback:
            progress_callback(i + 1, total, f"{island_info['name']} [{island_info['x']}:{island_info['y']}]")
        
        try:
            html = session.get(island_url + str(island_info["id"]))
            island = getIsland(html)
            
            island_data = {
                "id": island.get("id"),
                "name": island.get("name"),
                "x": island.get("x"),
                "y": island.get("y"),
                "tradegood": island.get("tradegood"),
                "resource_level": island.get("resourceLevel"),
                "tradegood_level": island.get("tradegoodLevel"),
                "wonder": island.get("wonder"),
                "wonder_name": island.get("wonderName"),
                "wonder_level": island.get("wonderLevel"),
                "cities": []
            }
            
            avatar_scores = island.get("avatarScores", {})
            
            for city in island.get("cities", []):
                if city.get("type") != "city":
                    continue
                
                player_id = city.get("Id", "")
                player_name = city.get("Name", "")
                player_scores = avatar_scores.get(player_id, {})
                
                city_data = {
                    "city_id": city.get("id"),
                    "city_name": city.get("name"),
                    "city_level": city.get("level", 0),
                    "player_id": player_id,
                    "player_name": player_name,
                    "player_state": city.get("state", ""),
                    "alliance_id": city.get("AllyId"),
                    "alliance_tag": city.get("AllyTag", ""),
                    "building_score": player_scores.get("building_score_main", "0"),
                }
                
                island_data["cities"].append(city_data)
                
                if player_name:
                    player_key = player_name.lower()
                    if player_key not in cache["players"]:
                        cache["players"][player_key] = {
                            "name": player_name,
                            "id": player_id,
                            "alliance_tag": city.get("AllyTag", ""),
                            "state": city.get("state", ""),
                            "islands": []
                        }
                    
                    if island.get("id") not in cache["players"][player_key]["islands"]:
                        cache["players"][player_key]["islands"].append(island.get("id"))
            
            cache["islands"].append(island_data)
            
            time.sleep(request_delay)
            
        except Exception as e:
            debug_log_error(f"Error scanning island {island_info['id']}", e)
    
    cache["total_players"] = len(cache["players"])
    debug_log(f"Cache built: {len(cache['islands'])} islands, {cache['total_players']} players")
    
    return cache


def get_player_islands_from_cache(cache, player_name):
    """
    Get list of island IDs where a player has cities from cache
    
    Returns
    -------
    dict with player_info and island_ids, or None if not found
    """
    if not cache:
        return None
    
    player_key = player_name.lower()
    
    player_info = cache.get("players", {}).get(player_key)
    if not player_info:
        for key, info in cache.get("players", {}).items():
            if player_name.lower() in key:
                player_info = info
                player_key = key
                break
    
    if not player_info:
        return None
    
    return {
        "name": player_info["name"],
        "id": player_info["id"],
        "alliance_tag": player_info.get("alliance_tag", ""),
        "island_ids": player_info.get("islands", [])
    }


def scan_islands_live(session, island_ids, progress_callback=None):
    """
    Scan specific islands for current live data
    
    Returns
    -------
    islands_data : list of full island data dicts
    """
    debug_log(f"Live scanning {len(island_ids)} islands...")
    
    islands_data = []
    total = len(island_ids)
    
    for i, island_id in enumerate(island_ids):
        if progress_callback:
            progress_callback(i + 1, total, f"Island {island_id}")
        
        try:
            html = session.get(island_url + str(island_id))
            island = getIsland(html)
            islands_data.append(island)
            time.sleep(0.2)
        except Exception as e:
            debug_log_error(f"Error scanning island {island_id}", e)
    
    return islands_data


def check_military_activity(city):
    """
    Check a city for military activity (blockade, occupation, fights)
    
    Returns
    -------
    activity : dict or None
    """
    infos = city.get("infos", {})
    if not infos:
        return None
    
    activity = {
        "city_id": city.get("id"),
        "city_name": city.get("name"),
        "city_level": city.get("level", 0),
        "player_id": city.get("Id"),
        "player_name": city.get("Name"),
        "alliance_tag": city.get("AllyTag", ""),
        "activities": []
    }
    
    if infos.get("armyAction") == "fight":
        activity["activities"].append({
            "type": "FIGHT",
            "description": "Combat in progress"
        })
    
    if infos.get("occupied"):
        occupier = infos.get("occupiedBy", {})
        activity["activities"].append({
            "type": "OCCUPIED",
            "description": "City is occupied",
            "occupier_name": occupier.get("name", "Unknown"),
            "occupier_id": occupier.get("odataId", ""),
            "occupier_alliance": occupier.get("allyTag", "")
        })
    
    if infos.get("blockaded"):
        blockader = infos.get("blockadedBy", {})
        activity["activities"].append({
            "type": "BLOCKADED", 
            "description": "Port is blockaded",
            "blockader_name": blockader.get("name", "Unknown"),
            "blockader_id": blockader.get("odataId", ""),
            "blockader_alliance": blockader.get("allyTag", "")
        })
    
    army_action = infos.get("armyAction", "")
    if army_action and army_action not in ["fight"]:
        activity["activities"].append({
            "type": "ARMY_ACTION",
            "description": f"Army action: {army_action}"
        })
    
    fleet_action = infos.get("fleetAction", "")
    if fleet_action:
        activity["activities"].append({
            "type": "FLEET_ACTION",
            "description": f"Fleet action: {fleet_action}"
        })
    
    if activity["activities"]:
        return activity
    return None


def compile_player_intel_hybrid(session, player_name, cache=None, progress_callback=None):
    """
    Compile player intel using cache for locations, then live scan for current data
    
    1. Search cache for player's island locations (instant)
    2. Live scan those specific islands (fast - only player's islands)
    3. Check for blockades/occupations
    4. Report all military activity on those islands
    5. Get all score categories from highscore
    """
    debug_log(f"Compiling hybrid intel for: {player_name}")
    
    intel = {
        "player_name": player_name,
        "player_id": None,
        "alliance_tag": None,
        "alliance_id": None,
        "state": None,
        "scores": {
            "total": {"score": 0, "rank": 0},
            "building": {"score": 0, "rank": 0},
            "research": {"score": 0, "rank": 0},
            "army": {"score": 0, "rank": 0},
            "trading": {"score": 0, "rank": 0},
            "gold": {"score": 0, "rank": 0},
        },
        "cities": [],
        "islands": [],
        "timestamp": datetime.now().isoformat(),
        "scan_type": "hybrid",
        "summary": {},
        "military_status": {
            "player_under_attack": False,
            "player_blockaded": False,
            "player_occupied": False,
            "player_activities": [],
            "island_activities": []
        }
    }
    
    island_ids = []
    
    if cache:
        cached_player = get_player_islands_from_cache(cache, player_name)
        if cached_player:
            intel["player_name"] = cached_player["name"]
            intel["player_id"] = cached_player["id"]
            intel["alliance_tag"] = cached_player["alliance_tag"]
            island_ids = cached_player["island_ids"]
            debug_log(f"Found {len(island_ids)} islands in cache for {player_name}")
    
    if not island_ids:
        debug_log(f"Player not in cache, trying highscore search...")
        player_info = search_player_by_name(session, player_name)
        if player_info:
            intel["player_id"] = player_info["id"]
            intel["player_name"] = player_info["name"]
            intel["scores"]["total"]["score"] = player_info.get("score", 0)
            intel["scores"]["total"]["rank"] = player_info.get("rank", 0)
        return intel
    
    player_info = search_player_by_name(session, player_name)
    if player_info:
        intel["player_id"] = player_info["id"]
        all_scores = get_all_player_scores(session, player_info["id"], intel["player_name"])
        intel["scores"] = all_scores
    
    if progress_callback:
        progress_callback(0, len(island_ids), "Starting live scan...")
    
    islands_data = scan_islands_live(session, island_ids, progress_callback)
    
    player_name_lower = player_name.lower()
    
    for island in islands_data:
        avatar_scores = island.get("avatarScores", {})
        
        for city in island.get("cities", []):
            if city.get("type") != "city":
                continue
            
            city_player_name = city.get("Name", "")
            is_target_player = city_player_name.lower() == player_name_lower
            
            military = check_military_activity(city)
            
            if military:
                military["island_id"] = island.get("id")
                military["island_name"] = island.get("name")
                military["island_coords"] = f"[{island.get('x')}:{island.get('y')}]"
                
                if is_target_player:
                    intel["military_status"]["player_activities"].append(military)
                    for act in military["activities"]:
                        if act["type"] == "OCCUPIED":
                            intel["military_status"]["player_occupied"] = True
                            intel["military_status"]["player_under_attack"] = True
                        elif act["type"] == "BLOCKADED":
                            intel["military_status"]["player_blockaded"] = True
                            intel["military_status"]["player_under_attack"] = True
                        elif act["type"] == "FIGHT":
                            intel["military_status"]["player_under_attack"] = True
                else:
                    intel["military_status"]["island_activities"].append(military)
            
            if is_target_player:
                player_id = city.get("Id", "")
                player_scores = avatar_scores.get(player_id, {})
                
                city_info = {
                    "city_id": city.get("id"),
                    "city_name": city.get("name"),
                    "city_level": city.get("level", 0),
                    "player_id": player_id,
                    "player_name": city.get("Name"),
                    "player_state": city.get("state", ""),
                    "alliance_id": city.get("AllyId"),
                    "alliance_tag": city.get("AllyTag", ""),
                    "island_id": island.get("id"),
                    "island_name": island.get("name"),
                    "island_x": island.get("x"),
                    "island_y": island.get("y"),
                    "island_tradegood": island.get("tradegood"),
                    "island_resource_level": island.get("resourceLevel"),
                    "island_tradegood_level": island.get("tradegoodLevel"),
                    "island_wonder": island.get("wonder"),
                    "island_wonder_name": island.get("wonderName"),
                    "island_wonder_level": island.get("wonderLevel"),
                    "island_cities_count": len([c for c in island.get("cities", []) if c.get("type") == "city"]),
                    "building_score": player_scores.get("building_score_main", "0"),
                    "research_score": player_scores.get("research_score_main", "0"),
                    "army_score": player_scores.get("army_score_main", "0"),
                    "is_blockaded": False,
                    "is_occupied": False,
                    "is_fighting": False,
                }
                
                if military:
                    for act in military["activities"]:
                        if act["type"] == "BLOCKADED":
                            city_info["is_blockaded"] = True
                            city_info["blockader"] = act.get("blockader_name", "Unknown")
                            city_info["blockader_alliance"] = act.get("blockader_alliance", "")
                        elif act["type"] == "OCCUPIED":
                            city_info["is_occupied"] = True
                            city_info["occupier"] = act.get("occupier_name", "Unknown")
                            city_info["occupier_alliance"] = act.get("occupier_alliance", "")
                        elif act["type"] == "FIGHT":
                            city_info["is_fighting"] = True
                
                intel["cities"].append(city_info)
                
                if not intel["alliance_id"] and city.get("AllyId"):
                    intel["alliance_id"] = city.get("AllyId")
                if not intel["alliance_tag"] and city.get("AllyTag"):
                    intel["alliance_tag"] = city.get("AllyTag")
    
    if not intel["player_id"] and intel["cities"]:
        intel["player_id"] = intel["cities"][0].get("player_id")
        intel["player_name"] = intel["cities"][0].get("player_name") or player_name
        
        if intel["player_id"]:
            all_scores = get_all_player_scores(session, intel["player_id"], intel["player_name"])
            intel["scores"] = all_scores
    
    if intel["cities"]:
        intel["state"] = intel["cities"][0].get("player_state", "")
        if intel["state"] == "":
            intel["state"] = "Active"
        elif intel["state"] == "inactive":
            intel["state"] = "Inactive"
        elif intel["state"] == "vacation":
            intel["state"] = "Vacation"
    
    island_summary = {}
    for city in intel["cities"]:
        isl_id = city["island_id"]
        if isl_id not in island_summary:
            island_summary[isl_id] = {
                "island_id": isl_id,
                "island_name": city["island_name"],
                "coords": f"[{city['island_x']}:{city['island_y']}]",
                "tradegood": city["island_tradegood"],
                "resource_level": city["island_resource_level"],
                "tradegood_level": city["island_tradegood_level"],
                "wonder": city["island_wonder"],
                "wonder_name": city["island_wonder_name"],
                "wonder_level": city["island_wonder_level"],
                "total_cities": city["island_cities_count"],
                "player_cities": 0,
                "player_city_names": []
            }
        island_summary[isl_id]["player_cities"] += 1
        island_summary[isl_id]["player_city_names"].append(city["city_name"])
    
    for isl_id, isl_data in island_summary.items():
        isl_data["miracle_estimate"] = estimate_miracle_usage(
            isl_data["total_cities"],
            isl_data["player_cities"]
        )
    
    intel["islands"] = list(island_summary.values())
    
    tradegood_names = {1: "Wine", 2: "Marble", 3: "Crystal", 4: "Sulfur"}
    intel["summary"] = {
        "total_cities": len(intel["cities"]),
        "total_islands": len(island_summary),
        "tradegood_distribution": {},
        "cities_blockaded": sum(1 for c in intel["cities"] if c.get("is_blockaded")),
        "cities_occupied": sum(1 for c in intel["cities"] if c.get("is_occupied")),
        "cities_fighting": sum(1 for c in intel["cities"] if c.get("is_fighting")),
    }
    
    for city in intel["cities"]:
        tg = tradegood_names.get(int(city["island_tradegood"]), "Unknown")
        intel["summary"]["tradegood_distribution"][tg] = intel["summary"]["tradegood_distribution"].get(tg, 0) + 1
    
    debug_log(f"Hybrid intel complete: {len(intel['cities'])} cities, {len(intel['military_status']['island_activities'])} island activities")
    return intel


def find_player_on_all_islands(session, player_name, player_id=None, progress_callback=None):
    """
    Search all islands on the server for a specific player
    
    Parameters
    ----------
    session : ikabot.web.session.Session
    player_name : str
    player_id : str, optional
        If provided, matches by ID instead of name (more reliable)
    progress_callback : callable, optional
        Called with (current, total, message) for progress updates
    
    Returns
    -------
    cities_data : list of dict
    """
    debug_log(f"Searching all islands for player: {player_name} (ID: {player_id})")
    
    cities_data = []
    
    islands = get_all_islands_with_players(session)
    
    if not islands:
        debug_log("No islands found or error getting islands")
        return cities_data
    
    total_islands = len(islands)
    
    for i, island_info in enumerate(islands):
        if progress_callback:
            progress_callback(i + 1, total_islands, f"Scanning {island_info['name']} [{island_info['x']}:{island_info['y']}]")
        
        try:
            html = session.get(island_url + str(island_info["id"]))
            island = getIsland(html)
            
            for city in island.get("cities", []):
                if city.get("type") != "city":
                    continue
                
                match = False
                if player_id:
                    match = str(city.get("Id", "")) == str(player_id)
                else:
                    match = city.get("Name", "").lower() == player_name.lower()
                
                if match:
                    avatar_scores = island.get("avatarScores", {})
                    city_player_id = city.get("Id", "")
                    player_scores = avatar_scores.get(city_player_id, {})
                    
                    city_info = {
                        "city_id": city.get("id"),
                        "city_name": city.get("name"),
                        "city_level": city.get("level", 0),
                        "player_id": city_player_id,
                        "player_name": city.get("Name"),
                        "player_state": city.get("state", ""),
                        "alliance_id": city.get("AllyId"),
                        "alliance_tag": city.get("AllyTag", ""),
                        "island_id": island.get("id"),
                        "island_name": island.get("name"),
                        "island_x": island.get("x"),
                        "island_y": island.get("y"),
                        "island_tradegood": island.get("tradegood"),
                        "island_resource_level": island.get("resourceLevel"),
                        "island_tradegood_level": island.get("tradegoodLevel"),
                        "island_wonder": island.get("wonder"),
                        "island_wonder_name": island.get("wonderName"),
                        "island_wonder_level": island.get("wonderLevel"),
                        "island_cities_count": len([c for c in island.get("cities", []) if c.get("type") == "city"]),
                        "building_score": player_scores.get("building_score_main", "0"),
                        "research_score": player_scores.get("research_score_main", "0"),
                        "army_score": player_scores.get("army_score_main", "0"),
                    }
                    
                    cities_data.append(city_info)
                    debug_log(f"Found city: {city_info['city_name']} on island {city_info['island_name']}")
            
            time.sleep(0.15)
            
        except Exception as e:
            debug_log_error(f"Error scanning island {island_info['id']}", e)
    
    debug_log(f"Total cities found for {player_name}: {len(cities_data)}")
    return cities_data


def estimate_miracle_usage(island_cities_count, player_cities_on_island):
    """
    Estimate how often a player can use the miracle if they have 100%+ priests
    
    Parameters
    ----------
    island_cities_count : int
    player_cities_on_island : int
    
    Returns
    -------
    estimate : str
    """
    if island_cities_count == 0:
        return "Unknown"
    
    player_share = player_cities_on_island / island_cities_count
    
    if player_share >= 1.0:
        return "Sole owner - unlimited activations"
    elif player_share >= 0.5:
        return f"~{int(player_share * 100)}% share - frequent activations"
    elif player_share >= 0.25:
        return f"~{int(player_share * 100)}% share - moderate activations"
    else:
        return f"~{int(player_share * 100)}% share - infrequent activations"


def compile_player_intel(session, player_name, full_scan=False, progress_callback=None):
    """
    Compile all intelligence on a player
    
    Parameters
    ----------
    session : ikabot.web.session.Session
    player_name : str
    full_scan : bool
        If True, scans ALL islands on the server (slow but complete)
        If False, only scans your islands (fast but limited)
    progress_callback : callable, optional
        For progress updates
    
    Returns
    -------
    intel : dict
    """
    debug_log(f"Compiling intel for player: {player_name} (full_scan={full_scan})")
    
    intel = {
        "player_name": player_name,
        "player_id": None,
        "alliance_tag": None,
        "alliance_id": None,
        "state": None,
        "scores": {
            "total": {"score": 0, "rank": 0}
        },
        "cities": [],
        "islands": [],
        "timestamp": datetime.now().isoformat(),
        "scan_type": "full" if full_scan else "local",
        "summary": {}
    }
    
    player_info = search_player_by_name(session, player_name)
    if player_info:
        intel["player_id"] = player_info["id"]
        intel["player_name"] = player_info["name"]
        intel["scores"]["total"]["score"] = player_info.get("score", 0)
        intel["scores"]["total"]["rank"] = player_info.get("rank", 0)
    
    if full_scan:
        cities = find_player_on_all_islands(
            session, 
            player_name, 
            player_id=intel.get("player_id"),
            progress_callback=progress_callback
        )
    else:
        cities = find_player_cities_local(session, player_name)
    
    intel["cities"] = cities
    
    if cities:
        intel["alliance_tag"] = cities[0].get("alliance_tag")
        intel["alliance_id"] = cities[0].get("alliance_id")
        intel["state"] = cities[0].get("player_state", "Active")
        if intel["state"] == "":
            intel["state"] = "Active"
        elif intel["state"] == "inactive":
            intel["state"] = "Inactive"
        elif intel["state"] == "vacation":
            intel["state"] = "Vacation"
        
        if cities[0].get("player_id"):
            intel["player_id"] = cities[0]["player_id"]
    
    island_summary = {}
    for city in cities:
        isl_id = city["island_id"]
        if isl_id not in island_summary:
            island_summary[isl_id] = {
                "island_id": isl_id,
                "island_name": city["island_name"],
                "coords": f"[{city['island_x']}:{city['island_y']}]",
                "tradegood": city["island_tradegood"],
                "resource_level": city["island_resource_level"],
                "tradegood_level": city["island_tradegood_level"],
                "wonder": city["island_wonder"],
                "wonder_name": city["island_wonder_name"],
                "wonder_level": city["island_wonder_level"],
                "total_cities": city["island_cities_count"],
                "player_cities": 0,
                "player_city_names": []
            }
        island_summary[isl_id]["player_cities"] += 1
        island_summary[isl_id]["player_city_names"].append(city["city_name"])
    
    for isl_id, isl_data in island_summary.items():
        isl_data["miracle_estimate"] = estimate_miracle_usage(
            isl_data["total_cities"], 
            isl_data["player_cities"]
        )
    
    intel["islands"] = list(island_summary.values())
    
    intel["summary"] = {
        "total_cities": len(cities),
        "total_islands": len(island_summary),
        "tradegood_distribution": {},
    }
    
    tradegood_names = {1: "Wine", 2: "Marble", 3: "Crystal", 4: "Sulfur"}
    for city in cities:
        tg = tradegood_names.get(int(city["island_tradegood"]), "Unknown")
        intel["summary"]["tradegood_distribution"][tg] = intel["summary"]["tradegood_distribution"].get(tg, 0) + 1
    
    debug_log(f"Intel compiled for {player_name}: {len(cities)} cities on {len(island_summary)} islands")
    return intel


def find_player_cities_local(session, player_name):
    """
    Find player cities only on islands where you have cities (fast but limited)
    """
    debug_log(f"Finding cities for player (local scan): {player_name}")
    
    cities_data = []
    scanned_islands = set()
    
    try:
        ids, own_cities = getIdsOfCities(session)
        for city_id in ids:
            html = session.get(city_url + city_id)
            city = getCity(html)
            island_id = city.get("islandId")
            
            if island_id and island_id not in scanned_islands:
                scanned_islands.add(island_id)
                
                html = session.get(island_url + island_id)
                island = getIsland(html)
                
                for city in island.get("cities", []):
                    if city.get("type") == "city" and city.get("Name", "").lower() == player_name.lower():
                        avatar_scores = island.get("avatarScores", {})
                        player_id = city.get("Id", "")
                        player_scores = avatar_scores.get(player_id, {})
                        
                        city_info = {
                            "city_id": city.get("id"),
                            "city_name": city.get("name"),
                            "city_level": city.get("level", 0),
                            "player_id": player_id,
                            "player_name": city.get("Name"),
                            "player_state": city.get("state", ""),
                            "alliance_id": city.get("AllyId"),
                            "alliance_tag": city.get("AllyTag", ""),
                            "island_id": island.get("id"),
                            "island_name": island.get("name"),
                            "island_x": island.get("x"),
                            "island_y": island.get("y"),
                            "island_tradegood": island.get("tradegood"),
                            "island_resource_level": island.get("resourceLevel"),
                            "island_tradegood_level": island.get("tradegoodLevel"),
                            "island_wonder": island.get("wonder"),
                            "island_wonder_name": island.get("wonderName"),
                            "island_wonder_level": island.get("wonderLevel"),
                            "island_cities_count": len([c for c in island.get("cities", []) if c.get("type") == "city"]),
                            "building_score": player_scores.get("building_score_main", "0"),
                            "research_score": player_scores.get("research_score_main", "0"),
                            "army_score": player_scores.get("army_score_main", "0"),
                        }
                        
                        cities_data.append(city_info)
                
                time.sleep(0.2)
        
        return cities_data
        
    except Exception as e:
        debug_log_error(f"Error in local scan for player: {player_name}", e)
        return cities_data


def display_player_intel(intel):
    """Display compiled player intelligence in a formatted way"""
    
    tradegood_names = {1: "Wine", 2: "Marble", 3: "Crystal", 4: "Sulfur"}
    wonder_names = {
        "1": "Hephaistos' Forge", "2": "Hades' Holy Grove", "3": "Demeter's Gardens",
        "4": "Athena's Parthenon", "5": "Temple of Hermes", "6": "Ares' Stronghold",
        "7": "Poseidon's Temple", "8": "Colossus"
    }
    
    print(f"\n{'='*60}")
    print(f"  PLAYER INTELLIGENCE REPORT")
    print(f"{'='*60}")
    print(f"  Player: {bcolors.CYAN}{intel['player_name']}{bcolors.ENDC}")
    print(f"  Player ID: {intel['player_id'] or 'Unknown'}")
    print(f"  Alliance: {intel['alliance_tag'] or 'None'}")
    print(f"  Status: {bcolors.GREEN if intel['state'] == 'Active' else bcolors.WARNING}{intel['state']}{bcolors.ENDC}")
    
    scores = intel.get('scores', {})
    total_score = scores.get('total', {})
    if total_score.get('score', 0) > 0:
        print(f"{'─'*60}")
        print(f"  SCORES")
        print(f"  {'Category':<15} {'Score':>12} {'Rank':>10}")
        print(f"  {'-'*37}")
        
        score_labels = {
            'total': 'Total',
            'building': 'Building',
            'research': 'Research', 
            'army': 'Army',
            'trading': 'Trading',
            'gold': 'Gold'
        }
        
        for key, label in score_labels.items():
            s = scores.get(key, {})
            score_val = s.get('score', 0)
            rank_val = s.get('rank', 0)
            if score_val > 0:
                print(f"  {label:<15} {bcolors.YELLOW}{score_val:>12,}{bcolors.ENDC} #{rank_val:>8}")
    
    scan_type = intel.get('scan_type', 'unknown')
    scan_labels = {'hybrid': 'Hybrid (Cache + Live)', 'full': 'Full Server', 'local': 'Local Islands', 'cache': 'Cache Only', 'targeted': 'Targeted'}
    print(f"{'─'*60}")
    print(f"  Scan Type: {scan_labels.get(scan_type, scan_type)}")
    print(f"  Report Time: {intel['timestamp']}")
    
    military = intel.get('military_status', {})
    if military.get('player_under_attack') or military.get('player_blockaded') or military.get('player_occupied'):
        print(f"{'─'*60}")
        print(f"  {bcolors.RED}⚠️  MILITARY ALERT  ⚠️{bcolors.ENDC}")
        if military.get('player_occupied'):
            print(f"  {bcolors.RED}● CITIES OCCUPIED{bcolors.ENDC}")
        if military.get('player_blockaded'):
            print(f"  {bcolors.WARNING}● PORTS BLOCKADED{bcolors.ENDC}")
        if military.get('player_under_attack') and not military.get('player_occupied') and not military.get('player_blockaded'):
            print(f"  {bcolors.WARNING}● COMBAT IN PROGRESS{bcolors.ENDC}")
    
    print(f"{'─'*60}")
    
    print(f"\n  SUMMARY")
    print(f"  Total Cities: {bcolors.YELLOW}{intel['summary']['total_cities']}{bcolors.ENDC}")
    print(f"  Total Islands: {intel['summary']['total_islands']}")
    
    blockaded = intel['summary'].get('cities_blockaded', 0)
    occupied = intel['summary'].get('cities_occupied', 0)
    fighting = intel['summary'].get('cities_fighting', 0)
    if blockaded or occupied or fighting:
        print(f"  Military Status: {bcolors.RED}{occupied} occupied, {blockaded} blockaded, {fighting} fighting{bcolors.ENDC}")
    
    print(f"  Resource Distribution:")
    for tg, count in intel['summary']['tradegood_distribution'].items():
        print(f"    {tg}: {count} cities")
    
    print(f"\n{'─'*60}")
    print(f"  ISLANDS & CITIES")
    print(f"{'─'*60}")
    
    for island in intel['islands']:
        tg_name = tradegood_names.get(int(island['tradegood']), "Unknown")
        wonder_name = wonder_names.get(str(island['wonder']), island.get('wonder_name', 'Unknown'))
        
        print(f"\n  {bcolors.CYAN}{island['island_name']}{bcolors.ENDC} {island['coords']}")
        print(f"    Resource: {tg_name} (Lv {island['tradegood_level']}) | Wood Lv {island['resource_level']}")
        print(f"    Miracle: {wonder_name} (Lv {island['wonder_level']})")
        print(f"    Cities: {island['player_cities']}/{island['total_cities']} ({island['miracle_estimate']})")
        print(f"    Player's Cities:")
        for city_name in island['player_city_names']:
            city_data = next((c for c in intel['cities'] if c['city_name'] == city_name), None)
            if city_data:
                status_flags = []
                if city_data.get('is_occupied'):
                    status_flags.append(f"{bcolors.RED}OCCUPIED by {city_data.get('occupier', '?')}[{city_data.get('occupier_alliance', '')}]{bcolors.ENDC}")
                if city_data.get('is_blockaded'):
                    status_flags.append(f"{bcolors.WARNING}BLOCKADED by {city_data.get('blockader', '?')}[{city_data.get('blockader_alliance', '')}]{bcolors.ENDC}")
                if city_data.get('is_fighting'):
                    status_flags.append(f"{bcolors.RED}FIGHTING{bcolors.ENDC}")
                
                status_str = f" - {', '.join(status_flags)}" if status_flags else ""
                print(f"      - {city_name} (Lv {city_data['city_level']}, ID: {city_data['city_id']}){status_str}")
    
    player_activities = military.get('player_activities', [])
    if player_activities:
        print(f"\n{'─'*60}")
        print(f"  {bcolors.RED}TARGET PLAYER MILITARY STATUS{bcolors.ENDC}")
        print(f"{'─'*60}")
        for activity in player_activities:
            print(f"\n  {activity['city_name']} on {activity['island_name']} {activity['island_coords']}")
            for act in activity['activities']:
                if act['type'] == 'OCCUPIED':
                    print(f"    {bcolors.RED}● OCCUPIED{bcolors.ENDC} by {act.get('occupier_name', 'Unknown')} [{act.get('occupier_alliance', '')}]")
                elif act['type'] == 'BLOCKADED':
                    print(f"    {bcolors.WARNING}● BLOCKADED{bcolors.ENDC} by {act.get('blockader_name', 'Unknown')} [{act.get('blockader_alliance', '')}]")
                elif act['type'] == 'FIGHT':
                    print(f"    {bcolors.RED}● COMBAT IN PROGRESS{bcolors.ENDC}")
                else:
                    print(f"    ● {act.get('description', act['type'])}")
    
    island_activities = military.get('island_activities', [])
    if island_activities:
        print(f"\n{'─'*60}")
        print(f"  {bcolors.WARNING}OTHER MILITARY ACTIVITY ON PLAYER'S ISLANDS{bcolors.ENDC}")
        print(f"{'─'*60}")
        for activity in island_activities:
            player_alliance = f"[{activity['alliance_tag']}]" if activity.get('alliance_tag') else ""
            print(f"\n  {activity['city_name']} ({activity['player_name']}{player_alliance})")
            print(f"    Island: {activity['island_name']} {activity['island_coords']}")
            for act in activity['activities']:
                if act['type'] == 'OCCUPIED':
                    print(f"    {bcolors.RED}● OCCUPIED{bcolors.ENDC} by {act.get('occupier_name', 'Unknown')} [{act.get('occupier_alliance', '')}]")
                elif act['type'] == 'BLOCKADED':
                    print(f"    {bcolors.WARNING}● BLOCKADED{bcolors.ENDC} by {act.get('blockader_name', 'Unknown')} [{act.get('blockader_alliance', '')}]")
                elif act['type'] == 'FIGHT':
                    print(f"    {bcolors.RED}● COMBAT IN PROGRESS{bcolors.ENDC}")
                else:
                    print(f"    ● {act.get('description', act['type'])}")
    
    print(f"\n{'='*60}\n")


def get_storage_path():
    """Get current storage path"""
    global _storage_path
    return _storage_path


def print_module_header(submenu_name=None, description=None):
    """
    Print the Spy Tool banner with optional submenu name and description
    
    Parameters
    ----------
    submenu_name : str, optional
        The current submenu name (e.g., "Player Spying")
    description : str, optional
        Brief description of what the submenu does
    """
    banner()
    print("\n")
    title = f"{MODULE_NAME.upper()} {MODULE_VERSION}"
    print("╔══════════════════════════════════════════════════════════╗")
    print(f"║ {title:^58} ║")
    
    if submenu_name:
        print("║──────────────────────────────────────────────────────────║")
        mode_line = f"║ {submenu_name:^58} ║"
        print(mode_line)
        
        if description:
            desc_line = f"║ {description:^58} ║"
            print(desc_line)
    
    print("╚══════════════════════════════════════════════════════════╝")
    print("")


def print_debug_status():
    """Print current debug status and log location"""
    debug_folder = get_debug_folder()
    status = "ENABLED" if DEBUG_ENABLED else "DISABLED"
    print(f"Debug logging: {status}")
    print(f"Debug folder: {debug_folder}")
    print()


def print_storage_status():
    """Print current storage location"""
    global _storage_path
    if _storage_path:
        print(f"Storage location: {_storage_path}")
    else:
        print("Storage location: NOT SET")
    print()


def toggle_debug():
    """Toggle debug logging on/off"""
    global DEBUG_ENABLED
    DEBUG_ENABLED = not DEBUG_ENABLED
    status = "ENABLED" if DEBUG_ENABLED else "DISABLED"
    debug_log(f"Debug logging toggled: {status}")
    return DEBUG_ENABLED


def settings_menu(session):
    """Settings submenu for debug and storage configuration"""
    while True:
        print_module_header("Settings", "Configure debug logging and storage location")
        
        print_debug_status()
        print_storage_status()
        
        cache = load_server_cache()
        if cache:
            cache_age = get_cache_age_str(cache)
            cache_path = get_server_cache_path()
            cache_size = cache_path.stat().st_size / (1024 * 1024) if cache_path and cache_path.exists() else 0
            print(f"Server Cache: {bcolors.GREEN}Available{bcolors.ENDC}")
            print(f"  Players: {cache.get('total_players', 0)}, Islands: {len(cache.get('islands', []))}")
            print(f"  Updated: {cache_age}, Size: {cache_size:.1f} MB")
        else:
            print(f"Server Cache: {bcolors.WARNING}Not built{bcolors.ENDC}")
        print()
        
        print("(1) Toggle debug logging")
        print("(2) Set custom storage location")
        print("(3) Use default storage location")
        print("(4) Test current storage location")
        print("(5) Test lock file system")
        print("(6) Delete server cache")
        print("(0) Back to main menu")
        print()
        print("(') Return to Ikabot main menu")
        print()
        
        choice = read(min=0, max=6, digit=True, additionalValues=["'"])
        
        if choice == "'" or choice == 0:
            return choice
        
        if choice == 1:
            status = toggle_debug()
            print(f"\nDebug logging is now: {'ENABLED' if status else 'DISABLED'}")
            enter()
        
        elif choice == 2:
            print("\nEnter custom storage path:")
            custom_path = read(msg="Path: ")
            if custom_path:
                result = initialize_storage(custom_path)
                if result:
                    print(f"\n{bcolors.GREEN}Storage location set to: {result}{bcolors.ENDC}")
                else:
                    print(f"\n{bcolors.RED}Failed to set storage location. Check path and permissions.{bcolors.ENDC}")
                enter()
        
        elif choice == 3:
            result = initialize_storage(None)
            if result:
                print(f"\n{bcolors.GREEN}Storage location set to default: {result}{bcolors.ENDC}")
            else:
                print(f"\n{bcolors.RED}Failed to set default storage location.{bcolors.ENDC}")
            enter()
        
        elif choice == 4:
            path = get_storage_path()
            if path:
                if test_storage_location(path):
                    print(f"\n{bcolors.GREEN}Storage location is working: {path}{bcolors.ENDC}")
                else:
                    print(f"\n{bcolors.RED}Storage location test FAILED: {path}{bcolors.ENDC}")
            else:
                print(f"\n{bcolors.RED}No storage location configured.{bcolors.ENDC}")
            enter()
        
        elif choice == 5:
            lock = get_lock()
            if lock:
                print("\nTesting lock file system...")
                if lock.acquire():
                    print(f"{bcolors.GREEN}Lock acquired successfully!{bcolors.ENDC}")
                    time.sleep(1)
                    lock.release()
                    print(f"{bcolors.GREEN}Lock released successfully!{bcolors.ENDC}")
                else:
                    print(f"{bcolors.RED}Failed to acquire lock (timeout).{bcolors.ENDC}")
            else:
                print(f"\n{bcolors.RED}No storage location configured.{bcolors.ENDC}")
            enter()
        
        elif choice == 6:
            cache_path = get_server_cache_path()
            if cache_path and cache_path.exists():
                print("\nAre you sure you want to delete the server cache? (y/n)")
                confirm = read(values=["y", "Y", "n", "N"])
                if confirm.lower() == "y":
                    try:
                        cache_path.unlink()
                        print(f"{bcolors.GREEN}Server cache deleted.{bcolors.ENDC}")
                    except Exception as e:
                        print(f"{bcolors.RED}Failed to delete cache: {e}{bcolors.ENDC}")
            else:
                print(f"\n{bcolors.WARNING}No server cache exists.{bcolors.ENDC}")
            enter()


def player_spying_menu(session):
    """Player spying submenu - gather intel without sending spies"""
    
    while True:
        print_module_header("Player Spying", "Gather intel on a player without sending spies")
        
        cache = load_server_cache()
        if cache:
            cache_age = get_cache_age_str(cache)
            player_count = cache.get("total_players", 0)
            print(f"Server Cache: {bcolors.GREEN}Available{bcolors.ENDC} ({player_count} players, updated {cache_age})")
        else:
            print(f"Server Cache: {bcolors.WARNING}Not available{bcolors.ENDC} - Build cache for instant searches")
        print()
        
        timing = load_scan_timing()
        if timing.get("samples"):
            avg_time = timing.get("avg_per_island", 0.2)
            print(f"Avg scan speed: {avg_time:.2f}s per island (based on {len(timing['samples'])} samples)")
            print()
        
        print("(1) Search player (uses cache if available)")
        print("(2) Build/Refresh server cache (takes 1-2 hours)")
        print("(3) View saved player reports")
        print("(4) Quick search (your islands only - no cache)")
        print("(5) View scan timing stats")
        print("")
        print("(0) Back to spy tool menu")
        print("(') Return to Ikabot main menu")
        print()
        
        choice = read(min=0, max=5, digit=True, additionalValues=["'"])
        
        if choice == "'":
            return "'"
        
        if choice == 0:
            return
        
        if choice == 1:
            result = search_player_with_cache(session, cache)
            if result == "'":
                return "'"
            elif result == "build_cache":
                result = build_cache_menu(session)
                if result == "'":
                    return "'"
        
        elif choice == 2:
            result = build_cache_menu(session)
            if result == "'":
                return "'"
        
        elif choice == 3:
            result = view_saved_reports()
            if result == "'":
                return "'"
        
        elif choice == 4:
            result = search_and_gather_intel(session, full_scan=False)
            if result == "'":
                return "'"
        
        elif choice == 5:
            show_scan_timing_stats()


def show_scan_timing_stats():
    """Display scan timing statistics"""
    timing = load_scan_timing()
    
    print("\n" + "="*50)
    print("  SCAN TIMING STATISTICS")
    print("="*50)
    
    if not timing.get("samples"):
        print("\nNo timing data collected yet.")
        print("Timing data is recorded each time you scan islands.")
        enter()
        return
    
    samples = timing["samples"]
    avg_per_island = timing.get("avg_per_island", 0.2)
    
    print(f"\nSamples collected: {len(samples)}")
    print(f"Average time per island: {avg_per_island:.3f} seconds")
    print()
    print("Recent scans:")
    print(f"{'Islands':<10} {'Time':<12} {'Per Island':<12} {'Date':<20}")
    print("-" * 54)
    
    for sample in samples[-10:]:
        islands = sample.get("islands", 0)
        time_taken = sample.get("time", 0)
        per_isl = sample.get("per_island", 0)
        ts = sample.get("timestamp", "")[:16]
        
        if time_taken > 3600:
            time_str = f"{time_taken/3600:.1f}h"
        elif time_taken > 60:
            time_str = f"{time_taken/60:.1f}m"
        else:
            time_str = f"{time_taken:.1f}s"
        
        print(f"{islands:<10} {time_str:<12} {per_isl:.3f}s{'':<6} {ts:<20}")
    
    print()
    print("Estimated scan times (based on your data):")
    for count in [10, 50, 100, 500, 1000, 2000]:
        est = count * avg_per_island
        if est > 3600:
            est_str = f"{est/3600:.1f} hours"
        elif est > 60:
            est_str = f"{est/60:.0f} minutes"
        else:
            est_str = f"{est:.0f} seconds"
        print(f"  {count:>5} islands: ~{est_str}")
    
    print()
    enter()


def build_cache_menu(session):
    """Menu for building/refreshing server cache"""
    
    print_module_header("Build Server Cache", "Scan all islands and store player data")
    
    cache = load_server_cache()
    if cache:
        cache_age = get_cache_age_str(cache)
        print(f"Current cache: {cache.get('total_players', 0)} players, updated {cache_age}")
        print()
    
    print("This will scan ALL islands on the server.")
    print("Estimated time: 1-2 hours (depending on server size)")
    print()
    print("Request delay options:")
    print("(1) Safe - 1.5s delay (~2 hours)")
    print("(2) Normal - 1.0s delay (~1.5 hours)")
    print("(3) Fast - 0.5s delay (~45 min) - May cause rate limiting")
    print()
    print("(0) Cancel")
    print("(') Return to Ikabot main menu")
    print()
    
    choice = read(min=0, max=3, digit=True, additionalValues=["'"])
    
    if choice == "'":
        return "'"
    
    if choice == 0:
        return
    
    delays = {1: 1.5, 2: 1.0, 3: 0.5}
    delay = delays.get(choice, 1.5)
    
    print(f"\n{bcolors.WARNING}Starting server scan with {delay}s delay...{bcolors.ENDC}")
    print("Progress: ", end="", flush=True)
    
    start_time = time.time()
    
    def progress_callback(current, total, message):
        pct = int(current / total * 100)
        elapsed = time.time() - start_time
        if current > 0:
            eta = (elapsed / current) * (total - current)
            eta_str = f"{int(eta // 60)}m {int(eta % 60)}s"
        else:
            eta_str = "calculating..."
        print(f"\rProgress: {current}/{total} ({pct}%) - ETA: {eta_str} - {message[:30]:<30}", end="", flush=True)
    
    try:
        new_cache = build_server_cache(session, progress_callback=progress_callback, request_delay=delay)
        print()
        
        if save_server_cache(new_cache):
            elapsed = time.time() - start_time
            print(f"\n{bcolors.GREEN}Cache built successfully!{bcolors.ENDC}")
            print(f"Islands: {len(new_cache.get('islands', []))}")
            print(f"Players: {new_cache.get('total_players', 0)}")
            print(f"Time: {int(elapsed // 60)}m {int(elapsed % 60)}s")
        else:
            print(f"\n{bcolors.RED}Failed to save cache.{bcolors.ENDC}")
        
        enter()
        
    except KeyboardInterrupt:
        print(f"\n\n{bcolors.WARNING}Cache building cancelled.{bcolors.ENDC}")
        enter()
    except Exception as e:
        debug_log_error("Error building cache", e)
        print(f"\n{bcolors.RED}Error building cache. Check debug logs.{bcolors.ENDC}")
        enter()
    
    return None


def search_player_with_cache(session, cache):
    """Search for a player using cache for locations, then live scan for current data"""
    
    has_cache = cache is not None
    cache_is_old = False
    
    if has_cache:
        try:
            cache_time = datetime.fromisoformat(cache.get("timestamp", ""))
            age_days = (datetime.now() - cache_time).days
            cache_is_old = age_days > 30
        except:
            cache_is_old = False
    
    if has_cache and not cache_is_old:
        cache_age = get_cache_age_str(cache)
        print_module_header("Player Search", f"Using cache for locations (updated {cache_age})")
        print("Will do LIVE scan of player's islands for current data + military status")
        
        print(f"\n{bcolors.WARNING}Note: Player names are case-sensitive (e.g., 'Skull' not 'skull'){bcolors.ENDC}")
        print("Enter player name (or ' to go back):")
        player_name = read(additionalValues=["'"])
        
        if player_name == "'" or not player_name:
            return "'" if player_name == "'" else None
        
        print(f"\nSearching for player: {bcolors.CYAN}{player_name}{bcolors.ENDC}")
        
        cached_player = get_player_islands_from_cache(cache, player_name)
        
        if cached_player:
            island_count = len(cached_player['island_ids'])
            print(f"{bcolors.GREEN}Found in cache!{bcolors.ENDC} Player has cities on {island_count} islands.")
            print(f"Live scanning those islands for current data...")
            print()
            
            def progress_callback(current, total, message):
                print(f"\rScanning: {current}/{total} islands - {message:<30}", end="", flush=True)
            
            intel = compile_player_intel_hybrid(session, player_name, cache, progress_callback)
            print()
            
            if intel and intel.get("cities"):
                display_player_intel(intel)
                return handle_intel_export(intel, session, cache)
            else:
                print(f"\n{bcolors.WARNING}Player found in cache but no cities found on live scan.{bcolors.ENDC}")
                print("This could mean:")
                print("  - Player name is case-sensitive (try exact capitalization)")
                print("  - Player has moved/deleted cities since cache was built")
                print("  - Cache is outdated")
                print()
                print("Options:")
                print("(1) Enter island coordinates manually")
                print(f"    Tip: You can use {bcolors.CYAN}www.ikalogs.ru/tools/map/{bcolors.ENDC} to find player city locations.")
                print("(2) Search within radius of coordinates")
                print("(0) Cancel")
                print("(') Return to Ikabot main menu")
                print()
                
                fallback = read(min=0, max=2, digit=True, additionalValues=["'"])
                
                if fallback == "'":
                    return "'"
                if fallback == 0:
                    return None
                if fallback == 1:
                    return search_player_manual_coords(session)
                if fallback == 2:
                    return search_player_radius(session)
                
                return None
        
        print(f"{bcolors.WARNING}Player not found in cache.{bcolors.ENDC}")
        print("Options:")
        print("(1) Enter island coordinates manually")
        print(f"    Tip: You can use {bcolors.CYAN}www.ikalogs.ru/tools/map/{bcolors.ENDC} to find player city locations.")
        print("(2) Search within radius of coordinates")
        print("(0) Cancel")
        print("(') Return to Ikabot main menu")
        print()
        
        fallback = read(min=0, max=2, digit=True, additionalValues=["'"])
        
        if fallback == "'":
            return "'"
        if fallback == 0:
            return None
        if fallback == 1:
            return search_player_manual_coords(session)
        if fallback == 2:
            return search_player_radius(session)
        
        return None
    
    else:
        if cache_is_old:
            print_module_header("Player Search", "Cache is outdated (>1 month old)")
            print(f"{bcolors.WARNING}Server cache is over 1 month old.{bcolors.ENDC}")
            print("Consider rebuilding the cache for accurate results.")
        else:
            print_module_header("Player Search", "No cache available")
            print(f"{bcolors.WARNING}No server cache found.{bcolors.ENDC}")
        
        print("\nOptions for finding a player:")
        print("")
        print("(1) Enter island coordinates manually (if you know where they are)")
        print(f"    Tip: You can use {bcolors.CYAN}www.ikalogs.ru/tools/map/{bcolors.ENDC} to find player city locations.")
        print("(2) Search within radius of coordinates (faster than full scan)")
        print("(3) Full server scan (1-2 hours) - also builds cache for future")
        print("(4) Build server cache first (recommended)")
        print("")
        print("(0) Back")
        print("(') Return to Ikabot main menu")
        print()
        
        method = read(min=0, max=4, digit=True, additionalValues=["'"])
        
        if method == "'":
            return "'"
        if method == 0:
            return None
        if method == 4:
            return "build_cache"
        
        if method == 1:
            return search_player_manual_coords(session)
        elif method == 2:
            return search_player_radius(session)
        elif method == 3:
            return search_player_full_scan(session)


def estimate_scan_time(island_count, delay=0.2):
    """Estimate scan time for a given number of islands"""
    seconds = island_count * delay
    if seconds < 60:
        return f"{int(seconds)} seconds"
    elif seconds < 3600:
        return f"{int(seconds / 60)} minutes"
    else:
        hours = seconds / 3600
        return f"{hours:.1f} hours"


def count_islands_in_radius(center_x, center_y, radius):
    """
    Estimate number of islands within a radius
    Ikariam map is 100x100, islands are roughly every 2-3 coords
    """
    area = 3.14159 * radius * radius
    islands_per_area = 0.15
    return int(area * islands_per_area)


def search_player_manual_coords(session):
    """Search for a player by manually entering island coordinates"""
    
    print_module_header("Manual Coordinate Search", "Enter known island coordinates")
    
    print("Enter island coordinates where you know the player has cities.")
    print(f"Tip: You can use {bcolors.CYAN}www.ikalogs.ru/tools/map/{bcolors.ENDC} to look up player city locations.")
    print("Format: x:y (e.g., 45:32)")
    print("Enter multiple separated by commas (e.g., 45:32, 50:40, 55:35)")
    print("Or enter 'done' when finished, or ' to go back")
    print()
    
    island_ids = []
    
    while True:
        coord_input = read(msg="Coordinates: ", additionalValues=["'", "done"])
        
        if coord_input == "'":
            return "'"
        if coord_input == "done" or not coord_input:
            break
        
        coords_list = [c.strip() for c in coord_input.split(",")]
        
        for coord in coords_list:
            try:
                parts = coord.replace(" ", "").split(":")
                x, y = int(parts[0]), int(parts[1])
                
                html = session.get(f"view=island&xcoord={x}&ycoord={y}")
                island = getIsland(html)
                
                if island and island.get("id"):
                    island_ids.append(island["id"])
                    print(f"  Found: {island['name']} [{x}:{y}] (ID: {island['id']})")
                else:
                    print(f"  No island at [{x}:{y}]")
                    
            except Exception as e:
                print(f"  Invalid coordinate: {coord}")
        
        print(f"\nTotal islands found: {len(island_ids)}")
        print("Enter more coordinates, 'done' to search, or ' to cancel")
    
    if not island_ids:
        print(f"\n{bcolors.WARNING}No islands specified.{bcolors.ENDC}")
        enter()
        return None
    
    print(f"\n{bcolors.WARNING}Note: Player names are case-sensitive (e.g., 'Skull' not 'skull'){bcolors.ENDC}")
    print(f"Enter player name to search for:")
    player_name = read(additionalValues=["'"])
    
    if player_name == "'" or not player_name:
        return "'" if player_name == "'" else None
    
    print(f"\nSearching {len(island_ids)} islands for: {bcolors.CYAN}{player_name}{bcolors.ENDC}")
    
    def progress_callback(current, total, message):
        print(f"\rScanning: {current}/{total} islands", end="", flush=True)
    
    intel = compile_player_intel_from_islands(session, player_name, island_ids, progress_callback)
    print()
    
    if not intel or not intel.get("cities"):
        print(f"\n{bcolors.RED}Player not found on specified islands.{bcolors.ENDC}")
        enter()
        return None
    
    display_player_intel(intel)
    return handle_intel_export(intel, session, None)


def search_player_radius(session):
    """Search for a player within a radius of given coordinates"""
    
    print_module_header("Radius Search", "Search islands within a radius")
    
    print("Enter center coordinates (x:y):")
    coord_input = read(additionalValues=["'"])
    
    if coord_input == "'":
        return "'"
    
    try:
        parts = coord_input.replace(" ", "").split(":")
        center_x, center_y = int(parts[0]), int(parts[1])
    except:
        print(f"{bcolors.RED}Invalid coordinates.{bcolors.ENDC}")
        enter()
        return None
    
    print(f"\nCenter: [{center_x}:{center_y}]")
    print("\nEnter search radius (in map units):")
    print("  5  = ~12 islands,  ~2 seconds")
    print("  10 = ~47 islands,  ~10 seconds")
    print("  15 = ~106 islands, ~21 seconds")
    print("  20 = ~188 islands, ~38 seconds")
    print("  30 = ~424 islands, ~1.5 minutes")
    print("  50 = ~1178 islands, ~4 minutes")
    print()
    
    radius = read(min=1, max=100, digit=True, msg="Radius: ", additionalValues=["'"])
    
    if radius == "'":
        return "'"
    
    est_islands = count_islands_in_radius(center_x, center_y, radius)
    est_time = estimate_scan_time(est_islands)
    
    print(f"\nEstimated: ~{est_islands} islands, ~{est_time}")
    print("Continue? (y/n)")
    
    confirm = read(values=["y", "Y", "n", "N", "'"])
    if confirm == "'" :
        return "'"
    if confirm.lower() != "y":
        return None
    
    print(f"\n{bcolors.WARNING}Note: Player names are case-sensitive (e.g., 'Skull' not 'skull'){bcolors.ENDC}")
    print(f"Enter player name to search for:")
    player_name = read(additionalValues=["'"])
    
    if player_name == "'" or not player_name:
        return "'" if player_name == "'" else None
    
    print(f"\nGetting islands in radius {radius} of [{center_x}:{center_y}]...")
    
    islands_shallow = get_all_islands_with_players(session)
    
    island_ids = []
    for island in islands_shallow:
        ix, iy = int(island["x"]), int(island["y"])
        distance = ((ix - center_x) ** 2 + (iy - center_y) ** 2) ** 0.5
        if distance <= radius:
            island_ids.append(island["id"])
    
    print(f"Found {len(island_ids)} islands with players in radius")
    
    if not island_ids:
        print(f"\n{bcolors.WARNING}No islands found in radius.{bcolors.ENDC}")
        enter()
        return None
    
    actual_time = estimate_scan_time(len(island_ids))
    print(f"Estimated scan time: {actual_time}")
    print(f"\nSearching for: {bcolors.CYAN}{player_name}{bcolors.ENDC}")
    
    start_time = time.time()
    
    def progress_callback(current, total, message):
        elapsed = time.time() - start_time
        if current > 0:
            eta = (elapsed / current) * (total - current)
            eta_str = f"ETA: {int(eta)}s"
        else:
            eta_str = ""
        print(f"\rScanning: {current}/{total} islands {eta_str:<20}", end="", flush=True)
    
    intel = compile_player_intel_from_islands(session, player_name, island_ids, progress_callback)
    print()
    
    elapsed = time.time() - start_time
    update_scan_timing(len(island_ids), elapsed)
    
    if not intel or not intel.get("cities"):
        print(f"\n{bcolors.RED}Player not found within radius.{bcolors.ENDC}")
        enter()
        return None
    
    display_player_intel(intel)
    return handle_intel_export(intel, session, None)


def search_player_full_scan(session):
    """Full server scan for a player (no cache)"""
    
    print_module_header("Full Server Scan", "Searching entire server")
    
    islands_shallow = get_all_islands_with_players(session)
    island_count = len(islands_shallow)
    est_time = estimate_scan_time(island_count)
    
    print(f"{bcolors.WARNING}WARNING: Full server scan{bcolors.ENDC}")
    print(f"Total islands with players: {island_count}")
    print(f"Estimated time: {est_time}")
    print()
    print("This is slow. Consider building a server cache instead.")
    print("Continue with full scan? (y/n)")
    
    confirm = read(values=["y", "Y", "n", "N", "'"])
    if confirm == "'":
        return "'"
    if confirm.lower() != "y":
        return None
    
    print(f"\n{bcolors.WARNING}Note: Player names are case-sensitive (e.g., 'Skull' not 'skull'){bcolors.ENDC}")
    print(f"Enter player name to search for:")
    player_name = read(additionalValues=["'"])
    
    if player_name == "'" or not player_name:
        return "'" if player_name == "'" else None
    
    print(f"\nSearching entire server for: {bcolors.CYAN}{player_name}{bcolors.ENDC}")
    
    start_time = time.time()
    
    def progress_callback(current, total, message):
        elapsed = time.time() - start_time
        if current > 0:
            eta = (elapsed / current) * (total - current)
            if eta > 3600:
                eta_str = f"ETA: {eta/3600:.1f}h"
            elif eta > 60:
                eta_str = f"ETA: {int(eta/60)}m"
            else:
                eta_str = f"ETA: {int(eta)}s"
        else:
            eta_str = ""
        pct = int(current / total * 100)
        print(f"\rScanning: {current}/{total} ({pct}%) {eta_str:<15}", end="", flush=True)
    
    intel = compile_player_intel(session, player_name, full_scan=True, progress_callback=progress_callback)
    print()
    
    elapsed = time.time() - start_time
    update_scan_timing(island_count, elapsed)
    
    if not intel or not intel.get("cities"):
        print(f"\n{bcolors.RED}Player not found on server.{bcolors.ENDC}")
        enter()
        return None
    
    display_player_intel(intel)
    return handle_intel_export(intel, session, None)


def compile_player_intel_from_islands(session, player_name, island_ids, progress_callback=None):
    """Compile player intel from a specific list of island IDs"""
    
    debug_log(f"Compiling intel for {player_name} from {len(island_ids)} specific islands")
    
    intel = {
        "player_name": player_name,
        "player_id": None,
        "alliance_tag": None,
        "alliance_id": None,
        "state": None,
        "scores": {
            "total": {"score": 0, "rank": 0},
            "building": {"score": 0, "rank": 0},
            "research": {"score": 0, "rank": 0},
            "army": {"score": 0, "rank": 0},
            "trading": {"score": 0, "rank": 0},
            "gold": {"score": 0, "rank": 0},
        },
        "cities": [],
        "islands": [],
        "timestamp": datetime.now().isoformat(),
        "scan_type": "targeted",
        "summary": {},
        "military_status": {
            "player_under_attack": False,
            "player_blockaded": False,
            "player_occupied": False,
            "player_activities": [],
            "island_activities": []
        }
    }
    
    islands_data = scan_islands_live(session, island_ids, progress_callback)
    
    player_name_lower = player_name.lower()
    found_player_id = None
    found_player_name = None
    
    for island in islands_data:
        avatar_scores = island.get("avatarScores", {})
        
        for city in island.get("cities", []):
            if city.get("type") != "city":
                continue
            
            city_player_name = city.get("Name", "")
            is_target_player = city_player_name.lower() == player_name_lower
            
            military = check_military_activity(city)
            
            if military:
                military["island_id"] = island.get("id")
                military["island_name"] = island.get("name")
                military["island_coords"] = f"[{island.get('x')}:{island.get('y')}]"
                
                if is_target_player:
                    intel["military_status"]["player_activities"].append(military)
                    for act in military["activities"]:
                        if act["type"] == "OCCUPIED":
                            intel["military_status"]["player_occupied"] = True
                            intel["military_status"]["player_under_attack"] = True
                        elif act["type"] == "BLOCKADED":
                            intel["military_status"]["player_blockaded"] = True
                            intel["military_status"]["player_under_attack"] = True
                        elif act["type"] == "FIGHT":
                            intel["military_status"]["player_under_attack"] = True
                else:
                    intel["military_status"]["island_activities"].append(military)
            
            if is_target_player:
                player_id = city.get("Id", "")
                player_scores = avatar_scores.get(player_id, {})
                
                
                if not found_player_id and player_id:
                    found_player_id = player_id
                    found_player_name = city.get("Name")
                city_info = {
                    "city_id": city.get("id"),
                    "city_name": city.get("name"),
                    "city_level": city.get("level", 0),
                    "player_id": player_id,
                    "player_name": city.get("Name"),
                    "player_state": city.get("state", ""),
                    "alliance_id": city.get("AllyId"),
                    "alliance_tag": city.get("AllyTag", ""),
                    "island_id": island.get("id"),
                    "island_name": island.get("name"),
                    "island_x": island.get("x"),
                    "island_y": island.get("y"),
                    "island_tradegood": island.get("tradegood"),
                    "island_resource_level": island.get("resourceLevel"),
                    "island_tradegood_level": island.get("tradegoodLevel"),
                    "island_wonder": island.get("wonder"),
                    "island_wonder_name": island.get("wonderName"),
                    "island_wonder_level": island.get("wonderLevel"),
                    "island_cities_count": len([c for c in island.get("cities", []) if c.get("type") == "city"]),
                    "building_score": player_scores.get("building_score_main", "0"),
                    "research_score": player_scores.get("research_score_main", "0"),
                    "army_score": player_scores.get("army_score_main", "0"),
                    "is_blockaded": False,
                    "is_occupied": False,
                    "is_fighting": False,
                }
                
                if military:
                    for act in military["activities"]:
                        if act["type"] == "BLOCKADED":
                            city_info["is_blockaded"] = True
                            city_info["blockader"] = act.get("blockader_name", "Unknown")
                            city_info["blockader_alliance"] = act.get("blockader_alliance", "")
                        elif act["type"] == "OCCUPIED":
                            city_info["is_occupied"] = True
                            city_info["occupier"] = act.get("occupier_name", "Unknown")
                            city_info["occupier_alliance"] = act.get("occupier_alliance", "")
                        elif act["type"] == "FIGHT":
                            city_info["is_fighting"] = True
                
                intel["cities"].append(city_info)
                
                if not intel["alliance_id"] and city.get("AllyId"):
                    intel["alliance_id"] = city.get("AllyId")
                if not intel["alliance_tag"] and city.get("AllyTag"):
                    intel["alliance_tag"] = city.get("AllyTag")
    

    if found_player_id:
        intel["player_id"] = found_player_id
        intel["player_name"] = found_player_name or player_name
        
        all_scores = get_all_player_scores(session, found_player_id, intel["player_name"])
        intel["scores"] = all_scores

    if intel["cities"]:
        intel["state"] = intel["cities"][0].get("player_state", "")
        if intel["state"] == "":
            intel["state"] = "Active"
        elif intel["state"] == "inactive":
            intel["state"] = "Inactive"
        elif intel["state"] == "vacation":
            intel["state"] = "Vacation"
    
    island_summary = {}
    for city in intel["cities"]:
        isl_id = city["island_id"]
        if isl_id not in island_summary:
            island_summary[isl_id] = {
                "island_id": isl_id,
                "island_name": city["island_name"],
                "coords": f"[{city['island_x']}:{city['island_y']}]",
                "tradegood": city["island_tradegood"],
                "resource_level": city["island_resource_level"],
                "tradegood_level": city["island_tradegood_level"],
                "wonder": city["island_wonder"],
                "wonder_name": city["island_wonder_name"],
                "wonder_level": city["island_wonder_level"],
                "total_cities": city["island_cities_count"],
                "player_cities": 0,
                "player_city_names": []
            }
        island_summary[isl_id]["player_cities"] += 1
        island_summary[isl_id]["player_city_names"].append(city["city_name"])
    
    for isl_id, isl_data in island_summary.items():
        isl_data["miracle_estimate"] = estimate_miracle_usage(
            isl_data["total_cities"],
            isl_data["player_cities"]
        )
    
    intel["islands"] = list(island_summary.values())
    
    tradegood_names = {1: "Wine", 2: "Marble", 3: "Crystal", 4: "Sulfur"}
    intel["summary"] = {
        "total_cities": len(intel["cities"]),
        "total_islands": len(island_summary),
        "tradegood_distribution": {},
        "cities_blockaded": sum(1 for c in intel["cities"] if c.get("is_blockaded")),
        "cities_occupied": sum(1 for c in intel["cities"] if c.get("is_occupied")),
        "cities_fighting": sum(1 for c in intel["cities"] if c.get("is_fighting")),
    }
    
    for city in intel["cities"]:
        tg = tradegood_names.get(int(city["island_tradegood"]), "Unknown")
        intel["summary"]["tradegood_distribution"][tg] = intel["summary"]["tradegood_distribution"].get(tg, 0) + 1
    
    return intel


def handle_intel_export(intel, session, cache):
    """Handle export options after displaying intel"""
    while True:
        print("Options:")
        print("(1) Export to file")
        print("(2) Search another player")
        print("(0) Back to menu")
        print("(') Return to Ikabot main menu")
        print()
        
        export_choice = read(min=0, max=2, digit=True, additionalValues=["'"])
        
        if export_choice == "'":
            return "'"
        
        if export_choice == 0:
            return None
        
        if export_choice == 1:
            export_player_intel(intel)
            return None
        
        elif export_choice == 2:
            return search_player_with_cache(session, cache)


def get_scan_timing_file():
    """Get path to scan timing data file"""
    storage = get_storage_path()
    if storage:
        return storage / "scan_timing.json"
    return None


def load_scan_timing():
    """Load scan timing data"""
    timing_file = get_scan_timing_file()
    if not timing_file or not timing_file.exists():
        return {"samples": [], "avg_per_island": 0.2}
    
    try:
        with open(timing_file, "r") as f:
            return json.load(f)
    except:
        return {"samples": [], "avg_per_island": 0.2}


def update_scan_timing(island_count, elapsed_time):
    """Update scan timing data with a new sample"""
    if island_count <= 0:
        return
    
    timing = load_scan_timing()
    
    per_island = elapsed_time / island_count
    timing["samples"].append({
        "islands": island_count,
        "time": elapsed_time,
        "per_island": per_island,
        "timestamp": datetime.now().isoformat()
    })
    
    if len(timing["samples"]) > 20:
        timing["samples"] = timing["samples"][-20:]
    
    if timing["samples"]:
        timing["avg_per_island"] = sum(s["per_island"] for s in timing["samples"]) / len(timing["samples"])
    
    timing_file = get_scan_timing_file()
    if timing_file:
        try:
            with open(timing_file, "w") as f:
                json.dump(timing, f, indent=2)
            debug_log(f"Updated scan timing: {per_island:.3f}s per island (avg: {timing['avg_per_island']:.3f}s)")
        except Exception as e:
            debug_log_error("Failed to save scan timing", e)


def search_and_gather_intel(session, full_scan=False):
    """Search for a player and gather intelligence"""
    
    scan_type = "Full Server" if full_scan else "Quick (Your Islands)"
    print_module_header("Player Search", f"Scan Type: {scan_type}")
    
    print("Enter player name (or ' to go back):")
    player_name = read(additionalValues=["'"])
    
    if player_name == "'" or not player_name:
        return "'" if player_name == "'" else None
    
    print(f"\nSearching for player: {bcolors.CYAN}{player_name}{bcolors.ENDC}")
    
    if full_scan:
        print(f"{bcolors.WARNING}Full server scan - this may take several minutes...{bcolors.ENDC}")
        print("Scanning islands: ", end="", flush=True)
        
        def progress_callback(current, total, message):
            pct = int(current / total * 100)
            print(f"\rScanning islands: {current}/{total} ({pct}%) - {message[:40]:<40}", end="", flush=True)
        
        intel = compile_player_intel(session, player_name, full_scan=True, progress_callback=progress_callback)
        print()
    else:
        print("Scanning your islands...")
        intel = compile_player_intel(session, player_name, full_scan=False)
    
    debug_log(f"Intel gathering complete for: {player_name}")
    
    if not intel["cities"]:
        print(f"\n{bcolors.RED}Could not find any cities for player: {player_name}{bcolors.ENDC}")
        if not full_scan:
            print("Try using Full Server Scan to search all islands.")
        else:
            print("The player may not exist or have no cities.")
        enter()
        return None
    
    display_player_intel(intel)
    
    while True:
        print("Options:")
        print("(1) Export to file")
        print("(2) Search another player")
        print("(0) Back to menu")
        print("(') Return to Ikabot main menu")
        print()
        
        export_choice = read(min=0, max=2, digit=True, additionalValues=["'"])
        
        if export_choice == "'":
            return "'"
        
        if export_choice == 0:
            return None
        
        if export_choice == 1:
            export_player_intel(intel)
            return None
        
        elif export_choice == 2:
            return search_and_gather_intel(session, full_scan=full_scan)


def export_player_intel(intel):
    """Export player intel to individual HTML and JSON files per player"""
    
    storage = get_storage_path()
    if not storage:
        print(f"\n{bcolors.RED}Storage location not configured. Go to Settings first.{bcolors.ENDC}")
        enter()
        return
    
    lock = get_lock()
    if not lock or not lock.acquire():
        print(f"\n{bcolors.RED}Could not acquire lock. Another process may be using the file.{bcolors.ENDC}")
        enter()
        return
    
    try:
        player_name = intel["player_name"]
        safe_name = re.sub(r'[^\w\-]', '_', player_name)
        
        player_folder = storage / "players"
        player_folder.mkdir(parents=True, exist_ok=True)
        
        json_file = player_folder / f"{safe_name}.json"
        html_file = player_folder / f"{safe_name}.html"
        
        if json_file.exists():
            with open(json_file, "r", encoding="utf-8") as f:
                old_intel = json.load(f)
            
            intel["previous_reports"] = old_intel.get("previous_reports", [])
            
            old_summary = {
                "timestamp": old_intel.get("timestamp"),
                "total_cities": old_intel.get("summary", {}).get("total_cities", 0),
                "scores": old_intel.get("scores", {}),
            }
            intel["previous_reports"].append(old_summary)
            
            if len(intel["previous_reports"]) > 10:
                intel["previous_reports"] = intel["previous_reports"][-10:]
        
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(intel, f, indent=2, ensure_ascii=False)
        
        debug_log(f"Saved JSON intel for {player_name} to {json_file}")
        
        generate_player_html_report(html_file, intel)
        
        print(f"\n{bcolors.GREEN}Intel exported successfully!{bcolors.ENDC}")
        print(f"JSON: {json_file}")
        print(f"HTML: {html_file}")
        enter()
        
    except Exception as e:
        debug_log_error(f"Error exporting intel for {intel.get('player_name')}", e)
        print(f"\n{bcolors.RED}Error exporting intel. Check debug logs.{bcolors.ENDC}")
        enter()
    finally:
        lock.release()


def extract_ajax_change_view_html(response):
    """Extract HTML payload from an ajax=1 response."""
    try:
        data = json.loads(response, strict=False)
        for item in data:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "changeView":
                if isinstance(item[1], list) and len(item[1]) >= 2:
                    return item[1][1]
    except Exception as e:
        debug_log_error("Failed to parse ajax changeView response", e)
    return ""


def parse_hideout_spy_stats(html_content):
    """
    Parse hideout page HTML and return spy/capacity data.

    Returns
    -------
    dict with keys:
        registered_spies, spies_in_city, spies_out, max_capacity, free_slots
    """
    stats = {
        "registered_spies": 0,
        "spies_in_city": 0,
        "spies_out": 0,
        "max_capacity": 0,
        "free_slots": 0,
    }

    if not html_content:
        return stats

    compact = re.sub(r"\s+", " ", html_content)

    key_patterns = {
        "registered_spies": [
            r"(?:registered|total)\s*sp(?:y|ies)[^0-9]{0,30}(\d+)",
            r"spiesRegistered\s*[:=]\s*(\d+)",
            r"agentsRegistered\s*[:=]\s*(\d+)",
        ],
        "spies_in_city": [
            r"(?:in\s*city|stationed|at\s*home)\s*sp(?:y|ies)[^0-9]{0,30}(\d+)",
            r"spiesInCity\s*[:=]\s*(\d+)",
            r"agentsAtHome\s*[:=]\s*(\d+)",
        ],
        "max_capacity": [
            r"(?:max(?:imum)?\s*)?(?:sp(?:y|ies)|agents)\s*(?:capacity|limit)[^0-9]{0,30}(\d+)",
            r"maxSpies\s*[:=]\s*(\d+)",
            r"agentsLimit\s*[:=]\s*(\d+)",
        ],
    }

    for key, patterns in key_patterns.items():
        for pattern in patterns:
            match = re.search(pattern, compact, re.IGNORECASE)
            if match:
                stats[key] = int(match.group(1))
                break

    if stats["registered_spies"] == 0 or stats["max_capacity"] == 0:
        ratio_match = re.search(r"(\d+)\s*/\s*(\d+)\s*(?:sp(?:y|ies)|agents)", compact, re.IGNORECASE)
        if ratio_match:
            left = int(ratio_match.group(1))
            right = int(ratio_match.group(2))
            if stats["registered_spies"] == 0:
                stats["registered_spies"] = left
            if stats["max_capacity"] == 0 and right >= left:
                stats["max_capacity"] = right

    if stats["spies_in_city"] == 0 and stats["registered_spies"] > 0:
        stats["spies_in_city"] = stats["registered_spies"]

    stats["spies_out"] = max(0, stats["registered_spies"] - stats["spies_in_city"])
    stats["free_slots"] = max(0, stats["max_capacity"] - stats["registered_spies"])

    return stats


def get_city_hideout_data(session, city_id):
    """Collect hideout and spy capacity data for one city."""
    city_html = session.get(city_url + str(city_id))
    city = getCity(city_html)

    city_data = {
        "city_id": str(city_id),
        "city_name": city.get("cityName", city.get("name", f"City {city_id}")),
        "island_id": city.get("islandId"),
        "x": city.get("x"),
        "y": city.get("y"),
        "hideout_found": False,
        "hideout_level": 0,
        "hideout_position": None,
        "registered_spies": 0,
        "spies_in_city": 0,
        "spies_out": 0,
        "max_capacity": 0,
        "free_slots": 0,
    }

    hideout_building = None
    for position in city.get("position", []):
        b = str(position.get("building", "")).lower()
        if b in ["safehouse", "hideout", "spyhouse"]:
            hideout_building = position
            break

    if not hideout_building:
        return city_data

    city_data["hideout_found"] = True
    city_data["hideout_level"] = int(hideout_building.get("level", 0) or 0)
    city_data["hideout_position"] = hideout_building.get("position")

    view_candidates = ["safeHouse", "safehouse", "espionage" ]
    hideout_html = ""
    for view in view_candidates:
        try:
            params = (
                f"view={view}&cityId={city_id}&position={city_data['hideout_position']}"
                f"&backgroundView=city&currentCityId={city_id}&ajax=1"
            )
            response = session.post(params)
            hideout_html = extract_ajax_change_view_html(response)
            if hideout_html:
                break
        except Exception as e:
            debug_log_error(f"Error fetching hideout view {view} for city {city_id}", e)

    stats = parse_hideout_spy_stats(hideout_html)
    city_data.update(stats)

    return city_data


def run_empire_spy_capacity_audit(session, progress_callback=None):
    """Scan all own cities and gather hideout/spy capacity information."""
    ids, _ = getIdsOfCities(session)
    total = len(ids)
    report = {
        "timestamp": datetime.now().isoformat(),
        "total_cities": total,
        "cities": [],
        "summary": {
            "cities_with_hideout": 0,
            "registered_spies": 0,
            "spies_in_city": 0,
            "spies_out": 0,
            "max_capacity": 0,
            "free_slots": 0,
        },
    }

    for i, city_id in enumerate(ids, 1):
        if progress_callback:
            progress_callback(i, total, f"City {city_id}")

        try:
            city_data = get_city_hideout_data(session, city_id)
            report["cities"].append(city_data)

            if city_data["hideout_found"]:
                report["summary"]["cities_with_hideout"] += 1
                report["summary"]["registered_spies"] += city_data["registered_spies"]
                report["summary"]["spies_in_city"] += city_data["spies_in_city"]
                report["summary"]["spies_out"] += city_data["spies_out"]
                report["summary"]["max_capacity"] += city_data["max_capacity"]
                report["summary"]["free_slots"] += city_data["free_slots"]
        except Exception as e:
            debug_log_error(f"Error auditing city {city_id}", e)

        time.sleep(0.15)

    return report


def display_spy_capacity_audit(report):
    """Print formatted audit report to terminal."""
    print(f"\n{'=' * 74}")
    print("  EMPIRE SPY CAPACITY AUDIT")
    print(f"{'=' * 74}")
    print(f"Timestamp: {report.get('timestamp', 'Unknown')}")
    print()
    print(f"{'City':<22} {'Coords':<10} {'Hideout':<8} {'Reg':>5} {'In':>5} {'Out':>5} {'Max':>5} {'Free':>5}")
    print("-" * 74)

    for city in report.get("cities", []):
        hideout = city.get("hideout_level", 0) if city.get("hideout_found") else "-"
        coords = f"[{city.get('x', '?')}:{city.get('y', '?')}]"
        print(
            f"{str(city.get('city_name', 'Unknown'))[:21]:<22} {coords:<10} {str(hideout):<8} "
            f"{city.get('registered_spies', 0):>5} {city.get('spies_in_city', 0):>5} {city.get('spies_out', 0):>5} "
            f"{city.get('max_capacity', 0):>5} {city.get('free_slots', 0):>5}"
        )

    summary = report.get("summary", {})
    print("-" * 74)
    print(
        f"TOTALS (hideout cities: {summary.get('cities_with_hideout', 0)}/{report.get('total_cities', 0)}): "
        f"reg={summary.get('registered_spies', 0)}, in={summary.get('spies_in_city', 0)}, "
        f"out={summary.get('spies_out', 0)}, max={summary.get('max_capacity', 0)}, free={summary.get('free_slots', 0)}"
    )
    print(f"{'=' * 74}\n")


def save_spy_capacity_audit(report):
    """Save audit report as JSON and HTML in storage folder."""
    storage = get_storage_path()
    if not storage:
        return None, None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = storage / "spy_audits"
    report_dir.mkdir(parents=True, exist_ok=True)

    json_file = report_dir / f"spy_capacity_audit_{stamp}.json"
    html_file = report_dir / f"spy_capacity_audit_{stamp}.html"

    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    rows = []
    for city in report.get("cities", []):
        hideout = city.get("hideout_level", 0) if city.get("hideout_found") else "-"
        rows.append(
            f"<tr><td>{city.get('city_name', 'Unknown')}</td>"
            f"<td>[{city.get('x', '?')}:{city.get('y', '?')}]</td>"
            f"<td>{hideout}</td>"
            f"<td>{city.get('registered_spies', 0)}</td>"
            f"<td>{city.get('spies_in_city', 0)}</td>"
            f"<td>{city.get('spies_out', 0)}</td>"
            f"<td>{city.get('max_capacity', 0)}</td>"
            f"<td>{city.get('free_slots', 0)}</td></tr>"
        )

    s = report.get("summary", {})
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset=\"UTF-8\">
  <title>Spy Capacity Audit</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 20px; background: #111; color: #eee; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
    th, td {{ border: 1px solid #333; padding: 8px; text-align: left; }}
    th {{ background: #1f4a7a; }}
    .summary {{ margin-top: 20px; padding: 10px; background: #1c1c2d; }}
  </style>
</head>
<body>
  <h1>Empire Spy Capacity Audit</h1>
  <p>Timestamp: {report.get('timestamp', 'Unknown')}</p>
  <table>
    <tr><th>City</th><th>Coords</th><th>Hideout</th><th>Registered</th><th>In City</th><th>Out</th><th>Max</th><th>Free</th></tr>
    {''.join(rows)}
  </table>
  <div class=\"summary\">
    <strong>Totals:</strong> Hideout cities: {s.get('cities_with_hideout', 0)}/{report.get('total_cities', 0)} |
    Registered: {s.get('registered_spies', 0)} |
    In city: {s.get('spies_in_city', 0)} |
    Out: {s.get('spies_out', 0)} |
    Max capacity: {s.get('max_capacity', 0)} |
    Free slots: {s.get('free_slots', 0)}
  </div>
</body>
</html>"""

    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)

    return json_file, html_file


def empire_spy_capacity_menu(session):
    """Phase 1: audit hideout and spy capacity across all own cities."""
    print_module_header("Empire Spy Capacity Audit", "Scan all cities for hideout and spy capacity")
    print("This scan checks each city for hideout level and spy counts.")
    print("It will then show:")
    print("  - Registered spies")
    print("  - Spies in city")
    print("  - Spies out of city")
    print("  - Free spy slots")
    print()
    print("Run scan now? (y/n)")

    confirm = read(values=["y", "Y", "n", "N", "'"])
    if confirm == "'":
        return "'"
    if confirm.lower() != "y":
        return None

    print("\nScanning cities: ", end="", flush=True)

    def progress_callback(current, total, message):
        pct = int((current / total) * 100) if total else 0
        print(f"\rScanning cities: {current}/{total} ({pct}%) - {message:<20}", end="", flush=True)

    report = run_empire_spy_capacity_audit(session, progress_callback=progress_callback)
    print()

    display_spy_capacity_audit(report)

    json_file, html_file = save_spy_capacity_audit(report)
    if json_file and html_file:
        print(f"Saved JSON: {json_file}")
        print(f"Saved HTML: {html_file}")
    else:
        print(f"{bcolors.WARNING}Storage not configured; report not saved to file.{bcolors.ENDC}")

    enter()
    return None


def estimate_hideout_level_for_target(townhall_level, known_hideout_level=None):
    """Estimate target hideout level using provided fallback formula."""
    if known_hideout_level is not None:
        return int(known_hideout_level)
    townhall_level = max(1, int(townhall_level or 1))
    # User requested formula: (50 - town hall level) / 2 + town hall level
    return int(round(((50 - townhall_level) / 2.0) + townhall_level))


def estimate_defending_spies(townhall_level, hideout_level):
    """Estimate defending spies from town hall and hideout level."""
    townhall_level = max(1, int(townhall_level or 1))
    hideout_level = max(townhall_level, int(hideout_level or townhall_level))
    # Conservative heuristic: defenders scale with hideout level and city maturity.
    base = max(1, int(round(hideout_level * 0.75)))
    maturity = int(round(townhall_level * 0.20))
    return base + maturity


def estimate_travel_seconds(source_x, source_y, target_x, target_y):
    """Estimate spy travel time from map distance."""
    if None in [source_x, source_y, target_x, target_y]:
        return 999999
    distance = ((int(source_x) - int(target_x)) ** 2 + (int(source_y) - int(target_y)) ** 2) ** 0.5
    # Approximation tuned for planning only.
    return int(distance * 90)


def format_duration(seconds):
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds}s"


def estimate_spy_outcome(attacking_spies, decoys, defending_spies, target_hideout_level):
    """Estimate success and discovery risk percentages."""
    attacking_spies = max(1, int(attacking_spies))
    decoys = max(0, int(decoys))
    defending_spies = max(1, int(defending_spies))
    target_hideout_level = max(1, int(target_hideout_level))

    attack_power = attacking_spies + decoys * 0.35
    defense_power = defending_spies + target_hideout_level * 0.35

    success = 20 + (75 * (attack_power / max(1.0, attack_power + defense_power)))
    success = max(5.0, min(99.0, success))

    discovery = (
        58.0
        - success * 0.45
        + (defending_spies / max(1, attacking_spies)) * 4.0
        + target_hideout_level * 0.22
        - decoys * 1.8
    )
    discovery = max(1.0, min(95.0, discovery))

    return round(success, 1), round(discovery, 1)


def estimate_spy_costs(attacking_spies, decoys):
    """Estimate gold and wine costs for dispatch planning."""
    attacking_spies = max(0, int(attacking_spies))
    decoys = max(0, int(decoys))
    gold_cost = attacking_spies * 120 + decoys * 40
    wine_cost = round(attacking_spies * 0.20 + decoys * 0.05, 1)
    return gold_cost, wine_cost


def load_player_report_files():
    """List available saved player JSON reports."""
    storage = get_storage_path()
    if not storage:
        return []
    players_folder = storage / "players"
    if not players_folder.exists():
        return []
    return sorted(players_folder.glob("*.json"))


def build_phase2_send_plan(target_cities, own_spy_audit, desired_spies, max_decoys=15):
    """Build optimized per-city send plan with success/risk/cost/time estimates."""
    plan = []

    own_sources = []
    for city in own_spy_audit.get("cities", []):
        available = city.get("spies_in_city", 0)
        if available <= 0:
            available = city.get("registered_spies", 0)
        if available > 0:
            own_sources.append({
                "city_id": city.get("city_id"),
                "city_name": city.get("city_name"),
                "x": city.get("x"),
                "y": city.get("y"),
                "available_spies": int(available),
            })

    for target in target_cities:
        townhall_level = int(target.get("city_level", 1) or 1)
        known_hideout = target.get("hideout_level")
        target_hideout = estimate_hideout_level_for_target(townhall_level, known_hideout)
        defenders = estimate_defending_spies(townhall_level, target_hideout)

        candidates = []
        for source in own_sources:
            max_send = min(source["available_spies"], desired_spies)
            if max_send <= 0:
                continue

            for decoys in range(0, min(max_decoys, source["available_spies"]) + 1):
                attackers = max_send
                if attackers + decoys > source["available_spies"]:
                    break

                success, discovery = estimate_spy_outcome(attackers, decoys, defenders, target_hideout)
                travel_seconds = estimate_travel_seconds(source["x"], source["y"], target.get("island_x"), target.get("island_y"))
                gold_cost, wine_cost = estimate_spy_costs(attackers, decoys)

                candidates.append({
                    "source_city_id": source["city_id"],
                    "source_city_name": source["city_name"],
                    "attackers": attackers,
                    "decoys": decoys,
                    "success": success,
                    "discovery": discovery,
                    "travel_seconds": travel_seconds,
                    "gold_cost": gold_cost,
                    "wine_cost": wine_cost,
                })

        if not candidates:
            plan.append({
                "target_city": target,
                "error": "No available source city with spies.",
                "estimated_defenders": defenders,
                "estimated_hideout": target_hideout,
            })
            continue

        # Priority requested: max success, min discovery, min decoys, min travel time.
        best = sorted(
            candidates,
            key=lambda c: (-c["success"], c["discovery"], c["decoys"], c["travel_seconds"]),
        )[0]

        plan.append({
            "target_city": target,
            "estimated_defenders": defenders,
            "estimated_hideout": target_hideout,
            "recommendation": best,
        })

    return plan


def display_phase2_send_plan(plan):
    """Display phase 2 send plan in terminal."""
    print(f"\n{'=' * 90}")
    print("  PHASE 2 SPY DISPATCH PLAN")
    print(f"{'=' * 90}")

    total_gold = 0
    total_wine = 0.0

    for entry in plan:
        target = entry.get("target_city", {})
        print(f"\nTarget: {target.get('city_name', 'Unknown')} [{target.get('island_x', '?')}:{target.get('island_y', '?')}]")
        print(
            f"  Est. Town Hall Lv: {target.get('city_level', '?')} | "
            f"Est. Hideout Lv: {entry.get('estimated_hideout', '?')} | "
            f"Est. Defending spies: {entry.get('estimated_defenders', '?')}"
        )

        if "error" in entry:
            print(f"  {bcolors.RED}{entry['error']}{bcolors.ENDC}")
            continue

        rec = entry["recommendation"]
        print(f"  Source city: {rec['source_city_name']} (ID: {rec['source_city_id']})")
        print(
            f"  Send spies: {rec['attackers']} + decoys: {rec['decoys']} | "
            f"Success: {bcolors.GREEN}{rec['success']}%{bcolors.ENDC} | "
            f"Discovery risk: {bcolors.WARNING}{rec['discovery']}%{bcolors.ENDC}"
        )
        print(
            f"  Est. travel: {format_duration(rec['travel_seconds'])} | "
            f"Gold: {rec['gold_cost']:,} | Wine: {rec['wine_cost']}"
        )

        total_gold += rec["gold_cost"]
        total_wine += rec["wine_cost"]

    print(f"\n{'-' * 90}")
    print(f"Totals: Gold {total_gold:,} | Wine {round(total_wine, 1)}")
    print(f"{'=' * 90}\n")


def phase2_dispatch_from_report_menu(session):
    """Phase 2 menu: choose report, choose targets, compute/confirm dispatch plan."""
    print_module_header("Targeted Spy Dispatch", "Choose report -> targets -> send plan")

    files = load_player_report_files()
    if not files:
        print(f"{bcolors.WARNING}No player report JSON files found in storage/players.{bcolors.ENDC}")
        print("Create at least one player report first from Player Spying.")
        enter()
        return None

    print("Available target report files:")
    report_rows = []
    for i, fpath in enumerate(files, 1):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            player_name = data.get("player_name", fpath.stem)
            city_count = len(data.get("cities", []))
            date = data.get("timestamp", "")[:16]
            report_rows.append((fpath, data))
            print(f"({i}) {player_name:<20} cities={city_count:<3} date={date}")
        except Exception:
            report_rows.append((fpath, None))
            print(f"({i}) {fpath.stem:<20} (unreadable)")

    print("(0) Back")
    print("(') Return to Ikabot main menu")
    print()

    choice = read(min=0, max=len(report_rows), digit=True, additionalValues=["'"])
    if choice == "'":
        return "'"
    if choice == 0:
        return None

    selected_path, selected_data = report_rows[choice - 1]
    if not selected_data:
        print(f"{bcolors.RED}Could not read selected JSON report.{bcolors.ENDC}")
        enter()
        return None

    target_cities = selected_data.get("cities", [])
    if not target_cities:
        print(f"{bcolors.WARNING}Selected report has no city entries.{bcolors.ENDC}")
        enter()
        return None

    print("\nTarget cities:")
    for i, city in enumerate(target_cities, 1):
        print(f"({i}) {city.get('city_name', 'Unknown'):<20} [{city.get('island_x', '?')}:{city.get('island_y', '?')}] Lv{city.get('city_level', '?')}")

    print("\nSelect target cities:")
    print("  (1) One city")
    print("  (2) Multiple cities (comma separated)")
    print("  (3) All cities")
    print("  (0) Cancel")
    print("  (') Return to Ikabot main menu")
    mode = read(min=0, max=3, digit=True, additionalValues=["'"])

    if mode == "'":
        return "'"
    if mode == 0:
        return None

    selected_targets = []
    if mode == 1:
        idx = read(min=1, max=len(target_cities), digit=True, msg="City number: ", additionalValues=["'"])
        if idx == "'":
            return "'"
        selected_targets = [target_cities[idx - 1]]
    elif mode == 2:
        raw = read(msg="City numbers (e.g. 1,3,5): ", additionalValues=["'"])
        if raw == "'":
            return "'"
        selected = set()
        for token in str(raw).split(","):
            token = token.strip()
            if token.isdigit():
                i = int(token)
                if 1 <= i <= len(target_cities):
                    selected.add(i)
        selected_targets = [target_cities[i - 1] for i in sorted(selected)]
    else:
        selected_targets = list(target_cities)

    if not selected_targets:
        print(f"{bcolors.WARNING}No valid target cities selected.{bcolors.ENDC}")
        enter()
        return None

    print("\nHow many primary spies should be sent per selected city?")
    desired_spies = read(min=1, max=200, digit=True, msg="Spies per city: ", additionalValues=["'"])
    if desired_spies == "'":
        return "'"

    print("\nBuilding own-city spy availability snapshot...")
    own_audit = run_empire_spy_capacity_audit(session)
    plan = build_phase2_send_plan(selected_targets, own_audit, desired_spies)
    display_phase2_send_plan(plan)

    print(f"Plan based on report: {selected_path}")
    print("These values are estimates. Send now? (y/n)")
    confirm = read(values=["y", "Y", "n", "N", "'"])
    if confirm == "'":
        return "'"
    if confirm.lower() != "y":
        return None

    print(f"\n{bcolors.WARNING}Live spy-dispatch endpoint mapping is not finalized yet for this server/build.{bcolors.ENDC}")
    print("No spies were sent. Plan generated and shown for confirmation/testing.")
    print("Next step: wire exact send endpoint and payload from your live hideout page responses.")
    enter()
    return None


def generate_player_html_report(html_file, intel):
    """Generate HTML report for a single player"""
    
    tradegood_names = {1: "Wine", 2: "Marble", 3: "Crystal", 4: "Sulfur"}
    wonder_names = {
        "1": "Hephaistos' Forge", "2": "Hades' Holy Grove", "3": "Demeter's Gardens",
        "4": "Athena's Parthenon", "5": "Temple of Hermes", "6": "Ares' Stronghold",
        "7": "Poseidon's Temple", "8": "Colossus"
    }
    
    player_name = intel.get('player_name', 'Unknown')
    state_class = f"status-{intel.get('state', 'active').lower()}"
    scores = intel.get('scores', {})
    total_score = scores.get('total', {})
    military = intel.get('military_status', {})
    
    alert_class = ""
    alert_text = ""
    if military.get('player_occupied'):
        alert_class = "alert-danger"
        alert_text = "⚠️ CITIES OCCUPIED"
    elif military.get('player_blockaded'):
        alert_class = "alert-warning"
        alert_text = "⚠️ PORTS BLOCKADED"
    elif military.get('player_under_attack'):
        alert_class = "alert-warning"
        alert_text = "⚠️ COMBAT IN PROGRESS"
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Spy Tool - {player_name}</title>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #1a1a2e; color: #eee; }}
        h1 {{ color: #00d4ff; border-bottom: 2px solid #00d4ff; padding-bottom: 10px; }}
        h2 {{ color: #00d4ff; margin-top: 30px; }}
        h3 {{ color: #ffd700; }}
        .player-card {{ background: #16213e; border-radius: 10px; padding: 20px; margin: 20px 0; }}
        .player-name {{ font-size: 24px; color: #00d4ff; }}
        .status-active {{ color: #00ff00; }}
        .status-inactive {{ color: #ff6600; }}
        .status-vacation {{ color: #ffff00; }}
        .island-card {{ background: #0f3460; border-radius: 8px; padding: 15px; margin: 10px 0; }}
        .city-list {{ margin-left: 20px; }}
        .timestamp {{ color: #888; font-size: 12px; }}
        .summary {{ display: flex; gap: 20px; flex-wrap: wrap; }}
        .summary-item {{ background: #0f3460; padding: 10px 20px; border-radius: 5px; }}
        .score {{ color: #ffd700; font-size: 18px; }}
        .score-table {{ background: #0f3460; border-radius: 8px; padding: 15px; margin: 15px 0; }}
        .score-table table {{ width: auto; min-width: 300px; }}
        .score-table td.score-value {{ color: #ffd700; text-align: right; }}
        .score-table td.rank-value {{ color: #aaa; text-align: right; }}
        .alert-danger {{ background: #8b0000; padding: 15px; border-radius: 8px; margin: 10px 0; font-weight: bold; }}
        .alert-warning {{ background: #8b4000; padding: 15px; border-radius: 8px; margin: 10px 0; font-weight: bold; }}
        .military-section {{ background: #2d1f1f; border: 2px solid #8b0000; border-radius: 8px; padding: 15px; margin: 15px 0; }}
        .occupied {{ color: #ff4444; font-weight: bold; }}
        .blockaded {{ color: #ffaa00; font-weight: bold; }}
        .fighting {{ color: #ff6666; font-weight: bold; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 10px; }}
        th, td {{ border: 1px solid #333; padding: 8px; text-align: left; }}
        th {{ background: #0f3460; }}
    </style>
</head>
<body>
    <h1>🕵️ Intelligence Report: {player_name}</h1>
    
    <div class="player-card">
        <div class="player-name">{player_name}</div>
        <p>Player ID: {intel.get('player_id', 'Unknown')} | 
           Alliance: {intel.get('alliance_tag') or 'None'} | 
           Status: <span class="{state_class}">{intel.get('state', 'Unknown')}</span></p>
        <p class="timestamp">Last updated: {intel.get('timestamp', 'Unknown')}</p>
        <p class="timestamp">Scan type: {intel.get('scan_type', 'unknown')}</p>
"""
    
    if alert_text:
        html += f'        <div class="{alert_class}">{alert_text}</div>\n'
    
    if total_score.get('score', 0) > 0:
        html += """
        <div class="score-table">
            <h3>📊 Scores</h3>
            <table>
                <tr><th>Category</th><th>Score</th><th>Rank</th></tr>
"""
        score_labels = {
            'total': 'Total',
            'building': 'Building',
            'research': 'Research',
            'army': 'Army',
            'trading': 'Trading',
            'gold': 'Gold'
        }
        
        for key, label in score_labels.items():
            s = scores.get(key, {})
            score_val = s.get('score', 0)
            rank_val = s.get('rank', 0)
            if score_val > 0:
                html += f'                <tr><td>{label}</td><td class="score-value">{score_val:,}</td><td class="rank-value">#{rank_val}</td></tr>\n'
        
        html += """            </table>
        </div>
"""
    
    blockaded = intel.get('summary', {}).get('cities_blockaded', 0)
    occupied = intel.get('summary', {}).get('cities_occupied', 0)
    fighting = intel.get('summary', {}).get('cities_fighting', 0)
    
    html += f"""
        <div class="summary">
            <div class="summary-item">Cities: {intel.get('summary', {}).get('total_cities', 0)}</div>
            <div class="summary-item">Islands: {intel.get('summary', {}).get('total_islands', 0)}</div>
"""
    
    if blockaded or occupied or fighting:
        html += f'            <div class="summary-item" style="background:#4a0000;">⚔️ {occupied} occupied, {blockaded} blockaded, {fighting} fighting</div>\n'
    
    html += """        </div>
        
        <h3>Resource Distribution</h3>
        <table>
            <tr><th>Resource</th><th>Cities</th></tr>
"""
    
    for tg, count in intel.get('summary', {}).get('tradegood_distribution', {}).items():
        html += f"            <tr><td>{tg}</td><td>{count}</td></tr>\n"
    
    html += """        </table>
        
        <h3>Islands & Cities</h3>
"""
    
    for island in intel.get('islands', []):
        tg_name = tradegood_names.get(int(island.get('tradegood', 0)), "Unknown")
        wonder_name = wonder_names.get(str(island.get('wonder', '')), island.get('wonder_name', 'Unknown'))
        
        html += f"""
        <div class="island-card">
            <strong>{island.get('island_name', 'Unknown')}</strong> {island.get('coords', '')}
            <br>Resource: {tg_name} (Lv {island.get('tradegood_level', '?')}) | Wood Lv {island.get('resource_level', '?')}
            <br>Miracle: {wonder_name} (Lv {island.get('wonder_level', '?')})
            <br>Cities: {island.get('player_cities', 0)}/{island.get('total_cities', 0)} - {island.get('miracle_estimate', '')}
            <div class="city-list">
"""
        
        for city_name in island.get('player_city_names', []):
            city_data = next((c for c in intel.get('cities', []) if c.get('city_name') == city_name), None)
            if city_data:
                status_parts = []
                if city_data.get('is_occupied'):
                    status_parts.append(f'<span class="occupied">OCCUPIED by {city_data.get("occupier", "?")} [{city_data.get("occupier_alliance", "")}]</span>')
                if city_data.get('is_blockaded'):
                    status_parts.append(f'<span class="blockaded">BLOCKADED by {city_data.get("blockader", "?")} [{city_data.get("blockader_alliance", "")}]</span>')
                if city_data.get('is_fighting'):
                    status_parts.append('<span class="fighting">COMBAT</span>')
                
                status_str = f" - {', '.join(status_parts)}" if status_parts else ""
                html += f"                • {city_name} (Lv {city_data.get('city_level', '?')}, ID: {city_data.get('city_id', '?')}){status_str}<br>\n"
        
        html += """
            </div>
        </div>
"""
    
    player_activities = military.get('player_activities', [])
    if player_activities:
        html += """
        <div class="military-section">
            <h3>🎯 Target Player Military Status</h3>
"""
        for activity in player_activities:
            html += f"            <p><strong>{activity['city_name']}</strong> on {activity['island_name']} {activity['island_coords']}</p>\n"
            html += "            <ul>\n"
            for act in activity['activities']:
                if act['type'] == 'OCCUPIED':
                    html += f'                <li class="occupied">OCCUPIED by {act.get("occupier_name", "Unknown")} [{act.get("occupier_alliance", "")}]</li>\n'
                elif act['type'] == 'BLOCKADED':
                    html += f'                <li class="blockaded">BLOCKADED by {act.get("blockader_name", "Unknown")} [{act.get("blockader_alliance", "")}]</li>\n'
                elif act['type'] == 'FIGHT':
                    html += '                <li class="fighting">COMBAT IN PROGRESS</li>\n'
                else:
                    html += f'                <li>{act.get("description", act["type"])}</li>\n'
            html += "            </ul>\n"
        html += "        </div>\n"
    
    island_activities = military.get('island_activities', [])
    if island_activities:
        html += """
        <div class="military-section" style="border-color: #8b4000;">
            <h3>⚔️ Other Military Activity on Player's Islands</h3>
            <table>
                <tr><th>City</th><th>Player</th><th>Alliance</th><th>Island</th><th>Status</th><th>Attacker</th></tr>
"""
        for activity in island_activities:
            for act in activity['activities']:
                attacker = ""
                status = act['type']
                if act['type'] == 'OCCUPIED':
                    attacker = f"{act.get('occupier_name', '?')} [{act.get('occupier_alliance', '')}]"
                    status = '<span class="occupied">OCCUPIED</span>'
                elif act['type'] == 'BLOCKADED':
                    attacker = f"{act.get('blockader_name', '?')} [{act.get('blockader_alliance', '')}]"
                    status = '<span class="blockaded">BLOCKADED</span>'
                elif act['type'] == 'FIGHT':
                    status = '<span class="fighting">FIGHTING</span>'
                
                html += f"""                <tr>
                    <td>{activity['city_name']}</td>
                    <td>{activity['player_name']}</td>
                    <td>{activity.get('alliance_tag', '')}</td>
                    <td>{activity['island_name']} {activity['island_coords']}</td>
                    <td>{status}</td>
                    <td>{attacker}</td>
                </tr>
"""
        html += """            </table>
        </div>
"""
    
    previous = intel.get('previous_reports', [])
    if previous:
        html += """
        <h3>History</h3>
        <table>
            <tr><th>Date</th><th>Cities</th><th>Score</th></tr>
"""
        for report in reversed(previous[-5:]):
            ts = report.get('timestamp', 'Unknown')[:10]
            cities = report.get('total_cities', '?')
            score = report.get('scores', {}).get('total', {}).get('score', '?')
            if isinstance(score, int):
                score = f"{score:,}"
            html += f"            <tr><td>{ts}</td><td>{cities}</td><td>{score}</td></tr>\n"
        html += "        </table>\n"
    
    html += """
    </div>
</body>
</html>"""
    
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    
    debug_log(f"Generated HTML report: {html_file}")


def view_saved_reports():
    """View saved player reports"""
    
    storage = get_storage_path()
    if not storage:
        print(f"\n{bcolors.RED}Storage location not configured. Go to Settings first.{bcolors.ENDC}")
        enter()
        return
    
    players_folder = storage / "players"
    if not players_folder.exists():
        print(f"\n{bcolors.WARNING}No saved reports found.{bcolors.ENDC}")
        enter()
        return
    
    json_files = list(players_folder.glob("*.json"))
    if not json_files:
        print(f"\n{bcolors.WARNING}No saved reports found.{bcolors.ENDC}")
        enter()
        return
    
    while True:
        print_module_header("Saved Reports", f"Found {len(json_files)} player reports")
        
        players_data = []
        for json_file in sorted(json_files):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    intel = json.load(f)
                players_data.append({
                    "file": json_file,
                    "name": intel.get("player_name", json_file.stem),
                    "cities": intel.get("summary", {}).get("total_cities", 0),
                    "timestamp": intel.get("timestamp", "Unknown")[:10],
                    "scan_type": intel.get("scan_type", "unknown")
                })
            except:
                pass
        
        if not players_data:
            print(f"\n{bcolors.WARNING}Could not read any reports.{bcolors.ENDC}")
            enter()
            return
        
        print(f"{'#':<3} {'Player':<20} {'Cities':<8} {'Scan':<8} {'Date':<12}")
        print("-" * 55)
        
        for i, player in enumerate(players_data, 1):
            scan = "Full" if player['scan_type'] == 'full' else "Local"
            print(f"({i:<2}) {player['name']:<20} {player['cities']:<8} {scan:<8} {player['timestamp']:<12}")
        
        print()
        print("(0) Back")
        print("(') Return to Ikabot main menu")
        print()
        
        choice = read(min=0, max=len(players_data), digit=True, additionalValues=["'"])
        
        if choice == "'":
            return "'"
        
        if choice == 0:
            return
        
        if isinstance(choice, int) and 1 <= choice <= len(players_data):
            player_data = players_data[choice - 1]
            
            try:
                with open(player_data["file"], "r", encoding="utf-8") as f:
                    intel = json.load(f)
                
                display_player_intel(intel)
                
                html_file = player_data["file"].with_suffix(".html")
                if html_file.exists():
                    print(f"HTML report: {html_file}")
                
                print()
                print("(1) Open file location")
                print("(0) Back to list")
                print("(') Return to Ikabot main menu")
                print()
                
                sub_choice = read(min=0, max=1, digit=True, additionalValues=["'"])
                
                if sub_choice == "'":
                    return "'"
                
                if sub_choice == 1:
                    print(f"\nFiles located at: {players_folder}")
                    enter()
                    
            except Exception as e:
                debug_log_error(f"Error reading report for {player_data['name']}", e)
                print(f"\n{bcolors.RED}Error reading report.{bcolors.ENDC}")
                enter()


def main_menu(session, event):
    """Main menu for Spy Tool"""
    debug_log("Spy Tool main menu loaded")
    
    if not initialize_storage():
        print(f"{bcolors.RED}Warning: Could not initialize default storage location.{bcolors.ENDC}")
        print("Please configure storage in Settings.\n")
        enter()
    
    while True:
        print_module_header("Main Menu", "Intelligence gathering and spy operations")
        
        print_storage_status()
        
        print("(1) Player Spying - Gather intel without sending spies")
        print("(2) Send Spy to City - Infiltrate and run missions")
        print("(3) Hideout Monitor - Auto-recruit spies")
        print("(4) Settings - Configure debug and storage")
        print("(5) Empire Spy Capacity Audit - Hideout/spies summary")
        print("(6) Targeted Spy Dispatch - Plan from saved JSON report")
        print("(0) Exit")
        print()
        print("(') Return to Ikabot main menu")
        print()
        
        choice = read(min=0, max=6, digit=True, additionalValues=["'"])
        
        debug_log(f"Main menu selection: {choice}")
        
        if choice == "'" or choice == 0:
            debug_log("Exiting Spy Tool")
            return
        
        if choice == 1:
            result = player_spying_menu(session)
            if result == "'":
                return
        
        elif choice == 2:
            print("\n[Send Spy to City - Coming in Phase 2]\n")
            enter()
        
        elif choice == 3:
            print("\n[Hideout Monitor - Coming in Phase 3]\n")
            enter()
        
        elif choice == 4:
            result = settings_menu(session)
            if result == "'":
                return

        elif choice == 5:
            result = empire_spy_capacity_menu(session)
            if result == "'":
                return

        elif choice == 6:
            result = phase2_dispatch_from_report_menu(session)
            if result == "'":
                return


def spyTool(session, event, stdin_fd, predetermined_input):
    """
    Spy Tool - Intelligence gathering and spy operations
    
    Parameters
    ----------
    session : ikabot.web.session.Session
    event : multiprocessing.Event
    stdin_fd: int
    predetermined_input : multiprocessing.managers.SyncManager.list
    """
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    
    debug_log("=" * 40)
    debug_log(f"Spy Tool {MODULE_VERSION} started")
    debug_log(f"Session user: {session.username if hasattr(session, 'username') else 'unknown'}")
    debug_log("=" * 40)
    
    try:
        main_menu(session, event)
    except KeyboardInterrupt:
        debug_log("Spy Tool interrupted by user")
    except Exception as e:
        debug_log_error("Spy Tool crashed", e)
        print(f"\n{bcolors.RED}An error occurred. Check debug logs for details.{bcolors.ENDC}")
        print(f"Debug folder: {get_debug_folder()}")
        enter()
    finally:
        event.set()
        debug_log("Spy Tool ended")
