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
mod("ikabot.helpers.varios", wait=lambda *a, **k: None)
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
