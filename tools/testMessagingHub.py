"""Offline test of messagingHub logic — stubs every ikabot import."""
import importlib.util, os, sys, types, tempfile, json

TMP = tempfile.mkdtemp()

def mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m

pkg = mod("ikabot"); pkg.__path__ = []
cfgmod = mod("ikabot.config", IKABOT_DATA_DIR=TMP, actionRequest="TOKEN",
             predetermined_input=[], autostart_active=False)
pkg.config = cfgmod
helpers = mod("ikabot.helpers"); helpers.__path__ = []
mod("ikabot.helpers.botComm", sendToBot=lambda *a, **k: None,
    notificationDataIsValid=lambda s: True)
class bcolors:
    GREEN = RED = WARNING = BLUE = ENDC = ""
mod("ikabot.helpers.gui", banner=lambda: None, bcolors=bcolors, enter=lambda: None)
mod("ikabot.helpers.pedirInfo", read=lambda **k: 0, getIdsOfCities=lambda s: ([], {}))
mod("ikabot.helpers.process", set_child_mode=lambda s: None)
mod("ikabot.helpers.signals", setInfoSignal=lambda s, i: None)
mod("ikabot.helpers.varios",
    wait=lambda *a, **k: None,
    addThousandSeparator=lambda n: "{:,}".format(int(n)).replace(",", "."),
    daysHoursMinutes=lambda s: "{}H {}M".format(int(s) // 3600, (int(s) % 3600) // 60),
    getDateTime=lambda: "2026-08-05 12:00:00")
mod("ikabot.helpers.getJson", getCity=lambda html: {})
cfgmod.city_url = "view=city&cityId="
mod("ikabot.helpers.logging", getLogger=lambda n: __import__("logging").getLogger(n))
# modulePrefs deliberately absent -> exercises the vanilla fallback path

spec = importlib.util.spec_from_file_location(
    "messagingHub", "/home/user/ikabot-modules/modules/messagingHub_v1.0.0.py")
hub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub)

assert hub._HAS_MODULE_PREFS is False, "should have fallen back to vanilla path"

class Session:
    username = "kurzon"; servidor = "s70-en"; mundo = "70"; word = "Poseidon"
    gf_lang = "en"; locale = "en_GB"
    _data = {"shared": {}}
    def getSessionData(self): return self._data
    def setSessionData(self, d, shared=False):
        (self._data["shared"] if shared else self._data).update(d)

s = Session()
fails = []
def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond: fails.append(name)

print("\n-- storage --")
check("hub dir is <base>/messaging_hub", hub._hub_dir(s) == os.path.join(TMP, "messaging_hub"))
check("files carry account key", hub._account_path_ok if False else
      os.path.basename(hub._config_path(s)) == "kurzon_s70-en70_config.json")
check("state file carries account key",
      os.path.basename(hub._state_path(s)) == "kurzon_s70-en70_state.json")
s.setSessionData({hub.SESSION_DIR_KEY: TMP + "/custom"}, shared=True)
os.makedirs(TMP + "/custom", exist_ok=True)
check("custom base honoured", hub._hub_dir(s) == os.path.join(TMP, "custom", "messaging_hub"))
s.setSessionData({hub.SESSION_DIR_KEY: ""}, shared=True)

print("\n-- config --")
cfg = hub._default_config()
check("all types have a route key", set(cfg["routes"]) == set(hub.EVENT_TYPES))
partial = hub._normalise_config({"destinations": [{"id": "d1", "name": "x", "kind": "ntfy"}],
                                 "routes": {"combat": ["d1"], "bogus": ["d9"]}})
check("normalise keeps good route", partial["routes"]["combat"] == ["d1"])
check("normalise drops unknown type", "bogus" not in partial["routes"])
check("normalise fills formatting", partial["formatting"]["body_max_chars"] == 900)
check("normalise survives junk", hub._normalise_config("nonsense")["config_version"] == 1)

hub._save_account_config(s, partial)
check("config round-trips", hub._load_account_config(s)["routes"]["combat"] == ["d1"])

print("\n-- global vs individual --")
g = hub._default_config()
g["destinations"] = [{"id": "g1", "name": "global-chan", "kind": "ntfy",
                      "ntfy": {"topic": "t"}, "enabled": True}]
g["routes"]["combat"] = ["g1"]
hub._save_global_config(s, g)
a = hub._load_account_config(s)
a["use_global"] = {"routing": True, "formatting": False}
hub._save_account_config(s, a)
eff = hub._effective_config(s)
check("global routing applied", eff["routes"]["combat"] == ["g1"])
check("global destinations applied", eff["destinations"][0]["name"] == "global-chan")
a["use_global"] = {"routing": False, "formatting": False}
hub._save_account_config(s, a)
check("individual routing restored", hub._effective_config(s)["routes"]["combat"] == ["d1"])

print("\n-- routing --")
cfg = hub._default_config()
cfg["destinations"] = [
    {"id": "d1", "name": "general", "kind": "ntfy", "ntfy": {"topic": "a"}, "enabled": True},
    {"id": "d2", "name": "combat", "kind": "ntfy", "ntfy": {"topic": "b"}, "enabled": True},
    {"id": "d3", "name": "off", "kind": "ntfy", "ntfy": {"topic": "c"}, "enabled": False},
]
cfg["routes"]["combat"] = ["d2", "d3"]
cfg["routes"]["player_message"] = ["d1"]
cfg["routes"]["news"] = ["d9"]
check("combat routes to d2 only (d3 disabled)",
      [d["id"] for d in hub._destinations_for(cfg, "combat")] == ["d2"])
check("dangling id ignored", hub._destinations_for(cfg, "news") == [])
cfg["type_enabled"]["player_message"] = False
check("disabled type routes nowhere", hub._destinations_for(cfg, "player_message") == [])
cfg["type_enabled"]["player_message"] = True
check("config runnable", hub._config_is_runnable(cfg))
check("empty config not runnable", not hub._config_is_runnable(hub._default_config()))

print("\n-- classification --")
langs = ["en"]
own = {"athens", "sparta"}
def cls(subject, body="", source="game", icon=""):
    return hub._classify({"subject": subject, "body": body, "source": source,
                          "icon": icon}, langs, [], own)
check("player mail", cls("Hi there", source="player") == "player_message")
check("combat source wins", cls("anything", source="combat") == "combat")
check("espionage", cls("Espionage report from Sparta") == "espionage")
check("piracy", cls("Your pirate raid was successful") == "piracy")
check("construction", cls("The Warehouse has been completed") == "construction")
check("research", cls("Research completed: Well Construction") == "research")
check("treaty", cls("Cultural treaty offer") == "treaty")
check("internal shipment via own city",
      cls("Transport arrived", "Your freighter from Athens has arrived") == "shipment_internal")
check("external shipment", cls("Trade", "PlayerX has delivered goods") == "shipment_external")
check("unknown falls to other", cls("Zzzz qqq") == "other")
check("override wins",
      hub._classify({"subject": "Zzzz qqq", "body": "", "source": "game", "icon": ""},
                    langs, [{"match": "subject_contains", "value": "zzzz", "type": "news"}],
                    own) == "news")
check("override cannot invent a type",
      hub._classify({"subject": "Zzzz qqq", "body": "", "source": "game", "icon": ""},
                    langs, [{"value": "zzzz", "type": "not_a_type"}], own) == "other")

print("\n-- parsing --")
row = '''<table><tr id="message123" class="">
 <td><input/></td><td><img/></td><td><span class="avatarName">PlayerOne</span></td>
 <td class="subject">Hello there</td><td>Athens</td><td>2026-08-04 10:00:00</td></tr>
 <tr id="tbl_mail123"><td class="msgText">Body text here</td></tr>
 <tr id="gmessage77" class="icon_construction">
 <td></td><td></td><td>Ikariam</td><td class="subject">Warehouse completed</td>
 <td>Sparta</td><td>2026-08-04 11:00:00</td></tr></table>'''
parsed = {m["id"]: m for m in hub._parse_messages_from_payload(row)}
check("player row parsed", parsed["m:123"]["sender"] == "PlayerOne")
check("subject parsed", parsed["m:123"]["subject"] == "Hello there")
check("body matched by suffix", parsed["m:123"]["body"] == "Body text here")
check("city/date parsed", parsed["m:123"]["city"] == "Athens")
check("game row is source=game", parsed["g:77"]["source"] == "game")
check("game row classified",
      hub._classify(parsed["g:77"], langs, [], own) == "construction")
check("canonical ids", hub._canonical_message_id("gMessage9") == "g:9"
      and hub._canonical_message_id("55") == "m:55")

print("\n-- formatting & filters --")
fmt = hub._default_config()["formatting"]
ev = {"id": "1", "type": "combat", "title": "Battle", "sender": "Foe",
      "body": "x" * 2000, "city": "Athens", "date": "now"}
text = hub._format_event_text(ev, fmt)
check("body truncated to limit", len(hub._event_body(ev, fmt)) <= 902)
check("text has label and sender", "Combat reports" in text and "Foe" in text)
fmt2 = dict(fmt); fmt2["include_body"] = False
check("body omitted when off", hub._event_body(ev, fmt2) == "")
fmt3 = dict(fmt); fmt3["mutes"] = ["battle"]
check("mute matches title", hub._is_muted(fmt3, ev))
check("mute misses unrelated", not hub._is_muted(fmt3, dict(ev, title="Peace", body="")))
embed = hub._discord_embed(ev, fmt, "footer")
check("embed within discord limits",
      len(embed["title"]) <= 256 and len(embed["description"]) <= 4096)
check("embed has type colour", embed["color"] == hub.DISCORD_COLORS["combat"])

print("\n-- quiet hours --")
import time as _t
def at(h):
    lt = list(_t.localtime()); lt[3] = h; lt[4] = 0
    return _t.mktime(_t.struct_time(lt))
q = {"quiet_hours": {"enabled": True, "from": "23:00", "to": "07:00", "types": []}}
check("inside overnight window", hub._in_quiet_hours(q, "combat", at(2)))
check("outside overnight window", not hub._in_quiet_hours(q, "combat", at(12)))
q2 = {"quiet_hours": {"enabled": True, "from": "09:00", "to": "17:00", "types": ["news"]}}
check("daytime window, listed type", hub._in_quiet_hours(q2, "news", at(10)))
check("daytime window, other type", not hub._in_quiet_hours(q2, "combat", at(10)))
check("disabled means never quiet",
      not hub._in_quiet_hours({"quiet_hours": {"enabled": False}}, "combat", at(2)))
check("bad time string is not fatal",
      not hub._in_quiet_hours({"quiet_hours": {"enabled": True, "from": "x", "to": "y"}},
                              "combat", at(2)))

print("\n-- seen ids --")
st = hub._load_state(s)
now = _t.time()
st["seen_ids"] = {"old": now - 40 * 86400, "new": now}
hub._prune_seen(st, 14)
check("old id pruned", "old" not in st["seen_ids"] and "new" in st["seen_ids"])
st["seen_ids"] = {str(i): now for i in range(hub.SEEN_HARD_CAP + 500)}
hub._prune_seen(st, 14)
check("hard cap enforced", len(st["seen_ids"]) == hub.SEEN_HARD_CAP)

print("\n-- delivery --")
sent_log = []
class FakeResp:
    def __init__(self, code=204): self.status_code = code; self.text = ""
    def json(self): return {}
class FakeRequests:
    def post(self, url, **kw): sent_log.append((url, kw)); return FakeResp()
hub._requests = lambda: FakeRequests()
st = hub._load_state(s)
events = [dict(ev, type="combat"), dict(ev, id="2", type="player_message")]
sent, failed = hub._send_events(s, cfg, events, st)
check("routed events delivered", sent == 2 and failed == 0)
check("each went to its own destination",
      any("/b" in u for u, _ in sent_log) and any("/a" in u for u, _ in sent_log))
sent_log.clear()
sent, failed = hub._send_events(s, cfg, [dict(ev, id="3", type="espionage")], st)
check("unrouted type sends nothing", sent == 0 and not sent_log)

class FailRequests:
    def post(self, url, **kw): return FakeResp(404)
hub._requests = lambda: FailRequests()
st2 = hub._load_state(s)
sent, failed = hub._send_events(s, cfg, [dict(ev, id="4", type="combat")], st2)
check("failure counted, no raise", sent == 0 and failed == 1 and st2["last_error"])

calls = {"n": 0}
class FlakyRequests:
    def post(self, url, **kw):
        calls["n"] += 1
        return FakeResp(204 if calls["n"] > 1 else 500)
hub._requests = lambda: FlakyRequests()
ok, detail = hub._send_ntfy({"ntfy": {"topic": "t"}}, [ev], fmt, "f")
check("500 is retried then succeeds", ok and calls["n"] == 2)

print("\n-- redaction --")
red = hub._redact('a https://discord.com/api/webhooks/123/abcDEF secret 123456789:AAbbCCddEEffGGhhIIjjKKllMMnn')
check("webhook redacted", "abcDEF" not in red)
check("bot token redacted", "AAbbCCddEEffGGhhIIjjKKllMMnn" not in red)

print("\n-- combat export --")
payload = '{"exportText":"Battle for Athens\\n----\\nRound 1\\nTriton engines ad\\nPlayerA lost 3 hoplites"}'
excerpt = hub._clean_export_excerpt(hub._extract_export_text(payload))
check("export excerpt cleaned",
      "Round 1" in excerpt and "Battle for" not in excerpt and "Triton" not in excerpt)

print()
if fails:
    print("{} FAILED: {}".format(len(fails), fails)); sys.exit(1)
print("all checks passed")

print("\n-- poll pipeline --")
INBOX = '''<html>currentCityId: 999,
<table><tr id="message123"><td></td><td></td><td><span class="avatarName">PlayerOne</span></td>
<td class="subject">Hello there</td><td>Athens</td><td>d1</td></tr>
<tr id="tbl_mail123"><td class="msgText">Body one</td></tr>
<tr id="gmessage77"><td></td><td></td><td>Ikariam</td>
<td class="subject">Warehouse has been completed</td><td>Sparta</td><td>d2</td></tr></table></html>'''
EXTRA = INBOX.replace('id="message123"', 'id="message124"').replace("Hello there", "Second mail")

class GameSession(Session):
    payload = INBOX
    def get(self, url=None, **kw):
        if url is None: raise AssertionError("bare session.get() is forbidden")
        return self.payload
    def post(self, url=None, **kw): return ""
    def setStatus(self, s): pass
    def logout(self): pass

gs = GameSession()
hub._requests = lambda: FakeRequests()
cfg2 = hub._default_config()
cfg2["destinations"] = [{"id": "d1", "name": "all", "kind": "ntfy",
                         "ntfy": {"topic": "a"}, "enabled": True}]
for t in hub.EVENT_TYPES:
    cfg2["routes"][t] = ["d1"]
hub._save_account_config(gs, cfg2)
st = hub._load_state(gs); st["seen_ids"] = {}; st["first_run_done"] = False
sent_log.clear()
sent, failed, scanned = hub._poll_messages(gs, cfg2, st, set(), ["en"])
check("first run forwards nothing", sent == 0 and not sent_log)
check("first run records ids as seen", len(st["seen_ids"]) == 2)

sent, failed, scanned = hub._poll_messages(gs, cfg2, st, set(), ["en"])
check("second poll, nothing new", sent == 0 and scanned == 0)

gs.payload = INBOX + EXTRA
sent, failed, scanned = hub._poll_messages(gs, cfg2, st, set(), ["en"])
check("new message forwarded once", sent == 1 and scanned == 1)
sent, failed, scanned = hub._poll_messages(gs, cfg2, st, set(), ["en"])
check("not forwarded again", sent == 0)

st2 = hub._load_state(gs); st2["seen_ids"] = {}; st2["first_run_done"] = False
cfg2["watchers"]["messages"]["notify_existing"] = True
sent, failed, scanned = hub._poll_messages(gs, cfg2, st2, set(), ["en"])
check("notify_existing forwards the backlog", sent == 3)

class DeadSession(GameSession):
    def get(self, url=None, **kw): raise RuntimeError("server down")
st3 = hub._load_state(gs)
try:
    hub._poll_messages(DeadSession(), cfg2, st3, set(), ["en"])
    check("dead server does not raise", True)
except Exception as e:
    check("dead server does not raise: {}".format(e), False)

print()
if fails:
    print("{} FAILED: {}".format(len(fails), fails)); sys.exit(1)
print("all checks passed")

print("\n-- lock: basics --")
import socket, threading
LP = hub._lock_path(s)
def clear_lock():
    try: os.remove(LP)
    except OSError: pass
def put_lock(**over):
    d = {"token": "other", "pid": os.getpid(), "host": socket.gethostname(),
         "account": "someone_s1-en1", "timestamp": _t.time()}
    d.update(over)
    with open(LP, "w") as f: json.dump(d, f)
    return d

clear_lock()
tok = hub._acquire_lock(s, timeout=2)
check("acquired on a free lock", bool(tok) and os.path.exists(LP))
check("lock is ours", hub._lock_is_ours(s, tok))
hub._release_lock(s, tok)
check("released removes the file", not os.path.exists(LP))

with hub._global_lock(s):
    check("context manager holds", os.path.exists(LP))
    with hub._global_lock(s):
        check("re-entrant, no self-deadlock", hub._lock_state["depth"] == 2)
    check("inner exit keeps the lock", os.path.exists(LP))
check("outer exit releases", not os.path.exists(LP))
check("depth reset", hub._lock_state["depth"] == 0)

print("\n-- lock: stale detection --")
clear_lock(); put_lock(timestamp=_t.time() - 10_000)
os.utime(LP, (_t.time() - 10_000, _t.time() - 10_000))
tok = hub._acquire_lock(s, timeout=2)
check("stale-by-time lock reclaimed", hub._lock_is_ours(s, tok))
hub._release_lock(s, tok)

clear_lock(); put_lock(pid=999_999)
check("dead holder on this host is stale", hub._holder_is_dead(hub._read_lock(LP)))
tok = hub._acquire_lock(s, timeout=2)
check("dead holder's lock reclaimed", hub._lock_is_ours(s, tok))
hub._release_lock(s, tok)

clear_lock(); put_lock(pid=999_999, host="some-other-machine")
check("dead pid on another host is NOT trusted",
      not hub._holder_is_dead(hub._read_lock(LP)))
try:
    hub._acquire_lock(s, timeout=1); check("fresh foreign lock not stolen", False)
except hub._LockBusy:
    check("fresh foreign lock not stolen", True)

print("\n-- lock: clock skew --")
clear_lock(); put_lock(timestamp=_t.time() - 10_000)   # old stamp, fresh mtime
check("old stamp + fresh mtime reads as young",
      hub._lock_age(LP, hub._read_lock(LP)) < hub.LOCK_STALE_SECONDS)
try:
    hub._acquire_lock(s, timeout=1)
    check("wrong clock cannot make us steal a live lock", False)
except hub._LockBusy:
    check("wrong clock cannot make us steal a live lock", True)
clear_lock(); put_lock(timestamp=_t.time() + 10_000)   # future stamp
check("future stamp does not go negative", hub._lock_age(LP, hub._read_lock(LP)) >= 0)

print("\n-- lock: corrupt and hostile --")
clear_lock()
with open(LP, "w") as f: f.write("{not json at all")
check("corrupt lock unreadable", hub._read_lock(LP) is None)
try:
    hub._acquire_lock(s, timeout=1); check("fresh corrupt lock not stolen", False)
except hub._LockBusy:
    check("fresh corrupt lock not stolen", True)
os.utime(LP, (_t.time() - 10_000, _t.time() - 10_000))
tok = hub._acquire_lock(s, timeout=2)
check("old corrupt lock reclaimed", hub._lock_is_ours(s, tok))

put_lock(token="stolen-by-someone-else")
hub._release_lock(s, tok)
check("we never delete a lock that is not ours", os.path.exists(LP))
check("the thief's lock is intact", hub._read_lock(LP)["token"] == "stolen-by-someone-else")
clear_lock()
hub._lock_state.update({"token": None, "depth": 0, "acquired_at": 0.0})

print("\n-- lock: unusable location --")
# Point the base at a path whose parent is a FILE. Unlike a missing directory,
# root cannot mkdir its way out of this, so the test means the same thing
# whether or not the runner is privileged.
_blocker = os.path.join(TMP, "not_a_directory")
with open(_blocker, "w") as f: f.write("x")
class GoneSession(Session):
    _data = {"shared": {hub.SESSION_DIR_KEY: os.path.join(_blocker, "sub")}}
try:
    hub._acquire_lock(GoneSession(), timeout=1)
    check("missing folder raises _LockUnavailable", False)
except hub._LockUnavailable:
    check("missing folder raises _LockUnavailable", True)
except Exception as e:
    check("missing folder raises _LockUnavailable, got {}".format(type(e).__name__), False)
ok, msg = hub._save_global_config(GoneSession(), hub._default_config())
check("unusable location fails gracefully, no raise", ok is False and msg)

print("\n-- global config: concurrent edits --")
clear_lock()
base = hub._default_config()
base["destinations"] = [{"id": "d1", "name": "shared", "kind": "ntfy",
                         "ntfy": {"topic": "x"}, "enabled": True}]
hub._save_global_config(s, dict(base))
a1 = hub._load_global_config(s)          # instance A reads
b1 = hub._load_global_config(s)          # instance B reads the same revision
a1["routes"]["combat"] = ["d1"]          # A changes routing only
b1["formatting"]["body_max_chars"] = 123 # B changes formatting only
check("A saved", hub._save_global_config(s, a1)[0])
check("B saved", hub._save_global_config(s, b1)[0])
final = hub._load_global_config(s)
check("A's routing survived B's save", final["routes"]["combat"] == ["d1"])
check("B's formatting survived", final["formatting"]["body_max_chars"] == 123)
check("revision incremented", final.get("revision", 0) >= 2)
check("snapshot never written to disk",
      hub.SNAPSHOT_KEY not in hub._read_json(hub._global_config_path(s)))

# A save that did not touch a section must never roll back another instance's
# change to it — this is the whole point of diffing against the snapshot.
d1_ = hub._load_global_config(s); d2_ = hub._load_global_config(s)
d1_["routes"]["news"] = ["d1"]
hub._save_global_config(s, d1_)
d2_["formatting"]["include_body"] = False      # d2 touches formatting only
hub._save_global_config(s, d2_)
untouched = hub._load_global_config(s)
check("a stale reader does not roll back a section it never edited",
      untouched["routes"]["news"] == ["d1"])

# A real same-section conflict: both instances change routes["news"] away from
# the value they read. Last writer wins, and the file stays intact.
e1 = hub._load_global_config(s); e2 = hub._load_global_config(s)
e1["routes"]["news"] = []                      # both differ from the snapshot ["d1"]
e2["routes"]["news"] = ["d1", "d1"]
hub._save_global_config(s, e1); hub._save_global_config(s, e2)
same = hub._load_global_config(s)
check("same-section conflict resolves to last writer, no corruption",
      same["routes"]["news"] == ["d1", "d1"] and same["destinations"][0]["name"] == "shared")

print("\n-- global config: lock stolen mid-write --")
before = hub._read_json(hub._global_config_path(s))
real_is_ours = hub._lock_is_ours
hub._lock_is_ours = lambda *a, **k: False          # always stolen
victim = hub._load_global_config(s)
victim["routes"]["piracy"] = ["d1"]
ok, msg = hub._save_global_config(s, victim)
check("stolen lock: save reports failure instead of crashing", ok is False and msg)
check("stolen lock: nothing was clobbered",
      hub._read_json(hub._global_config_path(s)) == before)

calls = {"n": 0}
def flaky_is_ours(*a, **k):
    calls["n"] += 1
    return calls["n"] > 1                          # stolen once, then fine
hub._lock_is_ours = flaky_is_ours
victim2 = hub._load_global_config(s)
victim2["routes"]["piracy"] = ["d1"]
ok, msg = hub._save_global_config(s, victim2)
hub._lock_is_ours = real_is_ours
check("stolen once: retried and applied", ok)
check("retry wrote the intended change",
      hub._load_global_config(s)["routes"]["piracy"] == ["d1"])
check("lock file cleaned up after all that", not os.path.exists(LP))

print("\n-- global config: busy holder --")
put_lock()
busy = hub._load_global_config(s)
busy["routes"]["news"] = ["d1"]
start = _t.time()
ok, msg = hub._save_global_config(s, busy, retries=1)
check("busy lock fails fast with a message", ok is False and "another instance" in msg)
check("busy wait respected the timeout", _t.time() - start >= 1)
clear_lock()

print("\n-- corrupt config recovery --")
gp = hub._global_config_path(s)
good = hub._read_json(gp)
with open(gp, "w") as f: f.write("}{ truncated")
rec = hub._read_json_resilient(gp)
check("corrupt global config recovered from .bak", rec is not None and "routes" in rec)
hub._write_json(gp, good, keep_backup=True)
check("hub still loads after recovery", hub._load_global_config(s) is not None)

print("\n-- ' navigation --")
real_read = hub.read
hub.read = lambda **k: "'"
try:
    hub._read(min=0, max=8, digit=True); check("' raises _BackToMenu", False)
except hub._BackToMenu:
    check("' raises _BackToMenu", True)
hub.read = lambda **k: k.get("additionalValues")
check("' is injected into additionalValues even when digits are required",
      "'" in hub._read(min=0, max=3, digit=True))
hub.read = lambda **k: 2
check("normal answers pass through", hub._read(min=0, max=3, digit=True) == 2)
hub.read = real_read

print("\n-- pid liveness --")
check("own pid is alive", hub._pid_alive(os.getpid()))
check("absurd pid is not alive", not hub._pid_alive(4_000_000))
check("garbage pid is not alive", not hub._pid_alive("nope") and not hub._pid_alive(None))

print()
if fails:
    print("{} FAILED: {}".format(len(fails), fails)); sys.exit(1)
print("all checks passed")

print("\n-- resource rules: reading a city --")
import re as _re

def city_html(res, capacity=10000, wine_spend=0, wine_prod=0):
    parts = [r"""maxResources: JSON.parse('{\"resource\":%d,""" % capacity,
             "wineSpendings: %d" % wine_spend]
    if wine_prod:
        parts.append('<td id="js_GlobalMenu_production_wine" class="c">%d</td>' % wine_prod)
    return "\n".join(parts)

def rule(**kw):
    base = {"id": "r1", "enabled": True, "scope": "global", "city_id": None,
            "resource": "wine", "mode": "absolute", "direction": "below",
            "threshold": 5000, "destinations": [], "cooldown_minutes": 0,
            "rearm_margin_percent": 10, "notify_on_recovery": False}
    base.update(kw); return base

city = {"name": "Athens", "availableResources": [1000, 4000, 3000, 2000, 100]}
h = city_html(None, capacity=10000, wine_spend=100)
check("warehouse capacity parsed", hub._warehouse_capacity(h) == 10000)
check("wine consumption parsed", hub._wine_consumption_per_hour(h) == 100)
check("wine production absent is 0", hub._wine_production_per_hour(h) == 0)
check("wine production parsed",
      hub._wine_production_per_hour(city_html(None, wine_prod=1234)) == 1234)

v, u = hub._rule_reading(rule(mode="absolute"), city, h)
check("absolute reading", v == 4000 and u == "")
v, u = hub._rule_reading(rule(mode="percent"), city, h)
check("percent reading", abs(v - 40.0) < 0.01 and u == "%")
v, u = hub._rule_reading(rule(mode="hours_left"), city, h)
check("hours-left reading", abs(v - 40.0) < 0.01 and u == "h")
v, _ = hub._rule_reading(rule(mode="hours_left"), city,
                         city_html(None, wine_spend=100, wine_prod=100))
check("wine not draining is not measurable", v is None)
v, _ = hub._rule_reading(rule(mode="hours_left", resource="wood"), city, h)
check("hours-left on non-wine is not measurable", v is None)
v, _ = hub._rule_reading(rule(mode="percent"), city, "no capacity here")
check("percent without capacity is not measurable", v is None)

print("\n-- resource rules: thresholds and re-arm --")
check("below breaches", hub._is_breach(rule(direction="below", threshold=5000), 4000))
check("below does not breach when above", not hub._is_breach(rule(direction="below", threshold=5000), 6000))
check("above breaches", hub._is_breach(rule(direction="above", threshold=5000), 6000))
r10 = rule(direction="below", threshold=5000, rearm_margin_percent=10)
check("not recovered inside the margin", not hub._has_recovered(r10, 5200))
check("recovered past the margin", hub._has_recovered(r10, 5600))
r10up = rule(direction="above", threshold=5000, rearm_margin_percent=10)
check("above-rule not recovered inside margin", not hub._has_recovered(r10up, 4800))
check("above-rule recovered past margin", hub._has_recovered(r10up, 4000))
check("zero threshold still gets slack",
      not hub._has_recovered(rule(threshold=0, rearm_margin_percent=10), 0))

print("\n-- resource rules: the anti-spam cycle --")
pedir = sys.modules["ikabot.helpers.pedirInfo"]
getjson = sys.modules["ikabot.helpers.getJson"]
WINE = {"v": 4000}
pedir.getIdsOfCities = lambda s: (["111"], {"111": {"name": "Athens"}})
getjson.getCity = lambda html: {"name": "Athens",
                                "availableResources": [0, WINE["v"], 0, 0, 0]}

class ResSession(Session):
    def get(self, url=None, **kw):
        if url is None: raise AssertionError("bare session.get()")
        return city_html(None, capacity=10000, wine_spend=100)
    def post(self, url=None, **kw): return ""
    def setStatus(self, s): pass
    def logout(self): pass

rs = ResSession()
rcfg = hub._default_config()
rcfg["destinations"] = [{"id": "d1", "name": "phone", "kind": "ntfy",
                         "ntfy": {"topic": "a"}, "enabled": True}]
rcfg["routes"]["resource_alert"] = ["d1"]
rcfg["watchers"]["resources"] = {"enabled": True, "interval_minutes": 30}
rcfg["resource_rules"] = [rule(id="r1", threshold=5000, cooldown_minutes=0,
                               notify_on_recovery=True)]
rstate = {"seen_ids": {}, "delivered": 0, "failed": 0, "resource_breaches": {}}

ev, checked = hub._check_resources(rs, rcfg, rstate)
check("breach fires once", len(ev) == 1 and checked == 1)
check("alert names the city and resource",
      "Athens" in ev[0]["title"] and ev[0]["type"] == "resource_alert")
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("still breached does NOT fire again", ev == [])
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("and does not fire on the third poll either", ev == [])

WINE["v"] = 5200                      # back over the line but inside the margin
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("recovery inside the margin does not re-arm", ev == [])
check("rule still flagged as breached",
      rstate["resource_breaches"]["r1:111"]["breached"] is True)
WINE["v"] = 4000                      # dips again — must NOT re-alert
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("flapping across the line sends nothing", ev == [])

WINE["v"] = 6000                      # clearly recovered
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("clear recovery re-arms and reports", len(ev) == 1 and "recovered" in ev[0]["title"])
check("rule re-armed", rstate["resource_breaches"]["r1:111"]["breached"] is False)
WINE["v"] = 4000
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("breaching again after re-arm fires", len(ev) == 1 and "recovered" not in ev[0]["title"])

print("\n-- resource rules: cooldown gates the re-arm --")
WINE["v"] = 4000
rcfg["resource_rules"] = [rule(id="r2", threshold=5000, cooldown_minutes=60)]
rstate["resource_breaches"] = {}
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("cooldown rule breaches", len(ev) == 1)
WINE["v"] = 9000
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("recovered but inside cooldown stays armed-off", ev == [] and
      rstate["resource_breaches"]["r2:111"]["breached"] is True)
rstate["resource_breaches"]["r2:111"]["fired_at"] = _t.time() - 3601
ev, _ = hub._check_resources(rs, rcfg, rstate)
check("after the cooldown it re-arms",
      rstate["resource_breaches"]["r2:111"]["breached"] is False)

print("\n-- resource rules: scope --")
pedir.getIdsOfCities = lambda s: (["111", "222"],
                                  {"111": {"name": "Athens"}, "222": {"name": "Sparta"}})
names = {"111": "Athens", "222": "Sparta"}
current = {"id": "111"}
class TwoCitySession(ResSession):
    def get(self, url=None, **kw):
        if url is None: raise AssertionError("bare session.get()")
        m = _re.search(r"cityId=(\d+)", url or "")
        if m: current["id"] = m.group(1)
        return city_html(None, capacity=10000, wine_spend=100)
getjson.getCity = lambda html: {"name": names[current["id"]],
                                "availableResources": [0, 4000, 0, 0, 0]}
tc = TwoCitySession()
rstate["resource_breaches"] = {}
rcfg["resource_rules"] = [rule(id="r3", scope="global", threshold=5000)]
ev, checked = hub._check_resources(tc, rcfg, rstate)
check("global rule covers every city", len(ev) == 2 and checked == 2)
rstate["resource_breaches"] = {}
rcfg["resource_rules"] = [rule(id="r4", scope="city", city_id="222",
                               city_name="Sparta", threshold=5000)]
ev, checked = hub._check_resources(tc, rcfg, rstate)
check("city rule covers only its city", len(ev) == 1 and ev[0]["city"] == "Sparta")
check("cities with no applicable rule are not fetched", checked == 1)

print("\n-- resource rules: routing and housekeeping --")
rcfg["resource_rules"] = [rule(id="r5", scope="global", threshold=5000,
                               destinations=["d2"])]
rcfg["destinations"].append({"id": "d2", "name": "loud", "kind": "ntfy",
                             "ntfy": {"topic": "b"}, "enabled": True})
rstate["resource_breaches"] = {}
ev, _ = hub._check_resources(tc, rcfg, rstate)
sent_log.clear()
hub._requests = lambda: FakeRequests()
hub._send_events(tc, rcfg, ev, rstate)
check("a rule's own destination overrides the route",
      all("/b" in u for u, _ in sent_log) and sent_log)
check("breach state recorded for both cities",
      len(rstate["resource_breaches"]) == 2)
rcfg["resource_rules"] = []
ev, _ = hub._check_resources(tc, rcfg, rstate)
check("no rules means no work", ev == [])
rcfg["resource_rules"] = [rule(id="r5", scope="city", city_id="111", threshold=5000)]
hub._check_resources(tc, rcfg, rstate)
check("stale breach keys are pruned",
      list(rstate["resource_breaches"]) == ["r5:111"])

print("\n-- resource rules: breach state survives a restart --")
rstate["resource_breaches"] = {}
hub._check_resources(tc, rcfg, rstate)
hub._save_state(tc, rstate)
reloaded = hub._load_state(tc)
ev, _ = hub._check_resources(tc, rcfg, reloaded)
check("a restart does not re-alert an already-known breach", ev == [])

print("\n-- resource rules: a dead city does not stop the sweep --")
class HalfDeadSession(TwoCitySession):
    def get(self, url=None, **kw):
        if url is None: raise AssertionError("bare session.get()")
        m = _re.search(r"cityId=(\d+)", url or "")
        if m and m.group(1) == "111": raise RuntimeError("timeout")
        if m: current["id"] = m.group(1)
        return city_html(None, capacity=10000, wine_spend=100)
rcfg["resource_rules"] = [rule(id="r6", scope="global", threshold=5000)]
rstate["resource_breaches"] = {}
ev, checked = hub._check_resources(HalfDeadSession(), rcfg, rstate)
check("one unreadable city does not abort the others", checked == 1 and len(ev) == 1)

print("\n-- resource rules: config validation --")
bad = hub._normalise_config({"resource_rules": [
    {"id": "ok", "resource": "wine", "threshold": 10},
    {"id": "bad-res", "resource": "gold", "threshold": 10},
    {"id": "bad-num", "resource": "wood", "threshold": "lots"},
    {"id": "fixed", "resource": "wood", "threshold": 5, "mode": "nonsense",
     "direction": "sideways", "scope": "galaxy"},
    "not even a dict",
]})
kept = {r["id"] for r in bad["resource_rules"]}
check("valid rule kept", "ok" in kept)
check("unknown resource dropped", "bad-res" not in kept)
check("non-numeric threshold dropped", "bad-num" not in kept)
check("garbage entry dropped", len(bad["resource_rules"]) == 2)
fixed = [r for r in bad["resource_rules"] if r["id"] == "fixed"][0]
check("bad mode/direction/scope repaired",
      fixed["mode"] == "absolute" and fixed["direction"] == "below"
      and fixed["scope"] == "global")

print("\n-- a lock problem never stops message forwarding --")
# The shared config becoming unreachable must degrade to "cannot save edits",
# never to "the hub stopped forwarding".
real_lock_write = hub._lock_write
hub._lock_write = lambda *a, **k: (_ for _ in ()).throw(OSError("share vanished"))
ok, msg = hub._save_global_config(s, hub._load_global_config(s) or hub._default_config())
check("save fails cleanly when the share disappears", ok is False and msg)

gs3 = GameSession()
hub._requests = lambda: FakeRequests()
cfg3 = hub._default_config()
cfg3["destinations"] = [{"id": "d1", "name": "all", "kind": "ntfy",
                         "ntfy": {"topic": "a"}, "enabled": True}]
for t in hub.EVENT_TYPES:
    cfg3["routes"][t] = ["d1"]
st5 = hub._load_state(gs3)
st5["seen_ids"] = {}; st5["first_run_done"] = True
sent, failed, scanned = hub._poll_messages(gs3, cfg3, st5, set(), ["en"])
check("forwarding still works while the share is unreachable", sent == 2 and failed == 0)
hub._lock_write = real_lock_write

print()
if fails:
    print("{} FAILED: {}".format(len(fails), fails)); sys.exit(1)
print("all checks passed")
