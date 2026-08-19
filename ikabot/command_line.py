#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import getpass
import multiprocessing
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from ikabot.config import *
from ikabot.function.activateMiracle import activateMiracle
from ikabot.function.alertAttacks import alertAttacks
from ikabot.function.alertLowWine import alertLowWine
from ikabot.function.attackBarbarians import attackBarbarians
from ikabot.function.autoBarbarians import autoBarbarians
from ikabot.function.autoPirate import autoPirate
from ikabot.function.buyResources import buyResources
from ikabot.function.checkForUpdate import checkForUpdate
from ikabot.function.constructBuilding import constructBuilding
from ikabot.function.constructionList import constructionList
from ikabot.function.decaptchaConf import decaptchaConf
from ikabot.function.distributeResources import distributeResources
from ikabot.function.donate import donate
from ikabot.function.donationBot import donationBot
from ikabot.function.dumpWorld import dumpWorld
from ikabot.function.getStatus import getStatus
from ikabot.function.importExportCookie import importExportCookie
from ikabot.function.research import research
from ikabot.function.consolidateResources import consolidateResources
from ikabot.function.killTasks import killTasks
from ikabot.function.loginDaily import loginDaily
from ikabot.function.logs import logs
from ikabot.function.proxyConf import proxyConf, show_proxy
from ikabot.function.searchForIslandSpaces import searchForIslandSpaces
from ikabot.function.sellResources import sellResources
from ikabot.function.sendResources import sendResources
from ikabot.function.shipMovements import shipMovements
from ikabot.function.stationArmy import stationArmy
from ikabot.function.notificationSetup import notificationSetup
from ikabot.function.externalModules import (
    get_external_modules,
    get_dirs,
    configure_directories,
    _run_external_module_child,
)
from ikabot.function.trainArmy import trainArmy
from ikabot.function.viewArmy import viewArmy
from ikabot.function.update import update
from ikabot.function.vacationMode import vacationMode
from ikabot.function.webServer import webServer
from ikabot.function.loadCustomModule import loadCustomModule
from ikabot.function.activateShrine import activateShrine
from ikabot.function.alertMessages import alertMessages
from ikabot.function.inactivePlayersRadiusMonitor import inactivePlayersRadiusMonitor
from ikabot.function.sendCulturalTreatyRequests import sendCulturalTreatyRequests
from ikabot.helpers.botComm import telegramDataIsValid, notificationDataIsValid
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import read
from ikabot.helpers.process import updateProcessList
from ikabot.web.session import *
from ikabot.function.UpgradeUnits import UpgradeUnits
from ikabot.function.modifyProduction import modifyProduction, modifyAcademyWorkers
from ikabot.function.reorganizeCityBuildings import reorganizeCityBuildings
from ikabot.function.developer import developer
from ikabot.helpers.pluginLoader import discover_plugins
from ikabot.helpers.modulePrefs import (
    is_autostart, set_autostart, list_autostart_modules, list_saved_modules,
)
from ikabot.helpers.credentialStore import (
    vault_exists, create_vault, open_vault,
    get_vault_location, set_vault_location,
    VaultWrongPasswordError, VaultCorruptError, VaultVersionError,
)


def _menu_actions():
    """The menu's action table: selection id -> module function.

    Module level so auto-start can resolve module names against the same table
    the menu dispatches from, rather than a second registry that could drift.
    """
    return {
        1: constructionList,
        2: sendResources,
        3: distributeResources,
        4: getStatus,
        5: activateShrine,
        6: loginDaily,
        701: alertAttacks,
        702: alertLowWine,
        703: alertMessages,
        801: buyResources,
        802: sellResources,
        901: donate,
        902: donationBot,
        10: vacationMode,
        11: activateMiracle,
        1201: trainArmy,
        1202: stationArmy,
        1203: UpgradeUnits,
        1204: viewArmy,
        13: shipMovements,
        14: constructBuilding,
        15: update,
        16: webServer,
        17: autoPirate,
        18: research,
        1901: attackBarbarians,
        1902: autoBarbarians,
        2001: searchForIslandSpaces,
        2002: dumpWorld,
        2003: inactivePlayersRadiusMonitor,
        2101: proxyConf,
        2102: notificationSetup,
        2103: killTasks,
        2104: decaptchaConf,
        2105: logs,
        2106: importExportCookie,
        2107: loadCustomModule,
        2108: developer,
        22: consolidateResources,
        2301: modifyProduction,
        2302: modifyAcademyWorkers,
        2303: reorganizeCityBuildings,
        25: sendCulturalTreatyRequests,
    }


def menu(session, checkUpdate=True):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    checkUpdate : bool
    """
    menu_actions = _menu_actions()

    while True:
        if checkUpdate:
            checkForUpdate()
            checkUpdate = False

        show_proxy(session)
        banner()

        modules = get_external_modules(session)
        process_list = updateProcessList(session)
        if len(process_list) > 0:
            table = process_list.copy()
            table.insert(
                0, {"pid": "pid", "action": "task", "date": "date", "status": "status"}
            )
            maxPid, maxAction, maxStatus = [
                max(i)
                for i in [
                    [len(str(r["pid"])) for r in table],
                    [len(str(r["action"])) for r in table],
                    [len(str(r["status"])) for r in table],
                ]
            ]
            print(
                "|{:^{maxPid}}|{:^{maxAction}}|{:^15}|{:^{maxStatus}}|".format(
                    table[0]["pid"],
                    table[0]["action"],
                    table[0]["date"],
                    table[0]["status"],
                    maxPid=maxPid,
                    maxAction=maxAction,
                    maxStatus=maxStatus,
                )
            )
            [
                print(
                    "|{:^{maxPid}}|{:^{maxAction}}|{:^15}|{:^{maxStatus}}|".format(
                        r["pid"],
                        r["action"],
                        datetime.datetime.fromtimestamp(r["date"]).strftime(
                            "%b %d %H:%M:%S"
                        ),
                        r["status"],
                        maxPid=maxPid,
                        maxAction=maxAction,
                        maxStatus=maxStatus,
                    )
                )
                for r in process_list
            ]
            print("")

        print("(0)  Exit")
        print("(1)  Construction list")
        print("(2)  Send resources")
        print("(3)  Distribute resources")
        print("(4)  Account status")
        print("(5)  Activate Shrine")
        print("(6)  Login daily")
        print("(7)  Alerts / Notifications")
        print("(8)  Marketplace")
        print("(9)  Donate")
        print("(10) Activate vacation mode")
        print("(11) Activate miracle")
        print("(12) Military actions")
        print("(13) See movements")
        print("(14) Construct building")
        print("(15) Update Ikabot")
        print("(16) Ikabot Web Server")
        print("(17) Auto-Pirate")
        print("(18) Research")
        print("(19) Attack / Grind barbarians")
        print("(20) Dump / Monitor world")
        print("(21) Options / Settings")
        print("(22) Consolidate resources")
        print("(23) City Management")
        print("(25) Send cultural treaty requests")

        plugins = discover_plugins()
        if plugins:
            print("(24) Plugins")

        if modules:
            print("")
            for i, (name, _path) in enumerate(modules):
                print(f"({i + 40}) {name}")
        print("(99) Configure external modules")
        print("(100) Refresh")

        top_max = 100
        selected = read(min=0, max=top_max, digit=True, empty=True)

        if selected == '':
            continue

        if selected == 7:
            banner()
            print("(0) Back")
            print("(1) Alert attacks")
            print("(2) Alert wine running out")
            print("(3) Alert in-game messages")
            selected = read(min=0, max=3, digit=True)
            if selected == 0:
                continue
            selected += 700

        if selected == 8:
            banner()
            print("(0) Back")
            print("(1) Buy resources")
            print("(2) Sell resources")
            selected = read(min=0, max=2, digit=True)
            if selected == 0:
                continue
            selected += 800

        if selected == 9:
            banner()
            print("(0) Back")
            print("(1) Donate once")
            print("(2) Donate automatically")
            selected = read(min=0, max=2, digit=True)
            if selected == 0:
                continue
            selected += 900

        if selected == 12:
            banner()
            print("(0) Back")
            print("(1) Train Army")
            print("(2) Send Troops/Ships")
            print("(3) Upgrade Army")
            print("(4) View Army")
            selected = read(min=0, max=4, digit=True)
            if selected == 0:
                continue
            selected += 1200

        if selected == 19:
            banner()
            print("(0) Back")
            print("(1) Simple Attack")
            print("(2) Auto Grind")
            selected = read(min=0, max=2, digit=True)
            if selected == 0:
                continue
            selected += 1900

        if selected == 20:
            banner()
            print("(0) Back")
            print("(1) Monitor islands")
            print("(2) Dump & Search world")
            print("(3) Monitor inactive players in radius")
            selected = read(min=0, max=3, digit=True)
            if selected == 0:
                continue
            selected += 2000

        if selected == 21:
            banner()
            print("(0) Back")
            print("(1) Configure Proxy")
            if notificationDataIsValid(session):
                print("(2) Notification Setup")
            else:
                print("(2) Notification Setup (not configured)")
            print("(3) Kill tasks")
            print("(4) Configure captcha resolver")
            print("(5) Logs")
            print("(6) Import / Export cookie")
            print("(7) Load custom ikabot module")
            print("(8) Developer Data")
            print("(9) Manage credential vault")
            print("(10) Auto-start modules")
            selected = read(min=0, max=10, digit=True)
            if selected == 0:
                continue
            if selected == 9:
                _manage_vault_menu(session)
                continue
            if selected == 10:
                _autostart_menu(session)
                continue
            selected += 2100

        if selected == 23:
            banner()
            print("(0) Back")
            print("(1) Set Production of Saw mill / Luxury good")
            print("(2) Set Academy workers")
            print("(3) Reorganize city buildings")
            selected = read(min=0, max=3, digit=True)
            if selected == 0:
                continue
            selected += 2300

        if selected == 99:
            configure_directories(session)
            continue

        if selected == 100:
            continue

        if isinstance(selected, int) and 40 <= selected < 40 + len(modules):
            mod_name, mod_path = modules[selected - 40]
            event = multiprocessing.Event()
            config.has_params = len(config.predetermined_input) > 0
            process = multiprocessing.Process(
                target=_run_external_module_child,
                args=(mod_path, session, event, sys.stdin.fileno(), config.predetermined_input),
                name=mod_name,
            )
            process.start()
            process_list.append({
                "pid": process.pid,
                "action": mod_name,
                "date": time.time(),
                "status": "started",
            })
            updateProcessList(session, programprocesslist=process_list)
            while not event.wait(timeout=2):
                if not process.is_alive():
                    break
            set_redraw_hook(None)
            print(f"\n'{mod_name}' is now running in the background.")
            time.sleep(0.8)
            continue

        if selected == 24 and plugins:
            banner()
            print("(0) Back")
            for i, plugin in enumerate(plugins, start=1):
                print(f"({i}) {plugin.label}")
            plugin_choice = read(min=0, max=len(plugins), digit=True)
            if plugin_choice == 0:
                continue
            chosen_plugin = plugins[plugin_choice - 1]
            event = multiprocessing.Event()
            config.has_params = len(config.predetermined_input) > 0
            process = multiprocessing.Process(
                target=chosen_plugin.entrypoint,
                args=(session, event, sys.stdin.fileno(), config.predetermined_input),
                name=chosen_plugin.name,
            )
            process.start()
            process_list.append({
                "pid": process.pid,
                "action": chosen_plugin.name,
                "date": time.time(),
                "status": "started",
            })
            updateProcessList(session, programprocesslist=process_list)
            while not event.wait(timeout=2):
                if not process.is_alive():
                    break
            set_redraw_hook(None)
            print(f"\n'{chosen_plugin.name}' is now running in the background.")
            time.sleep(0.8)
            continue

        if selected == 0:
            if isWindows:
                # in unix, you can exit ikabot and close the terminal and the
                # processes will continue to execute; in windows they will die
                print("Closing this console will kill the processes.")
                enter()
            clear()
            os._exit(0)

        if selected not in menu_actions:
            continue

        try:
            event = multiprocessing.Event()
            config.has_params = len(config.predetermined_input) > 0
            process = multiprocessing.Process(
                target=menu_actions[selected],
                args=(session, event, sys.stdin.fileno(), config.predetermined_input),
                name=menu_actions[selected].__name__,
            )
            process.start()
            process_list.append(
                {
                    "pid": process.pid,
                    "action": menu_actions[selected].__name__,
                    "date": time.time(),
                    "status": "started",
                }
            )
            updateProcessList(session, programprocesslist=process_list)
            while not event.wait(timeout=2):
                if not process.is_alive():
                    break
            set_redraw_hook(None)
            print(f"\n'{menu_actions[selected].__name__}' is now running in the background.")
            time.sleep(0.8)
        except KeyboardInterrupt:
            pass


def init():
    home = "USERPROFILE" if isWindows else "HOME"
    os.chdir(os.getenv(home))
    os.makedirs(IKABOT_SESSIONS_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Credential vault helpers
# ---------------------------------------------------------------------------

def _prompt_vault_login():
    """Prompt for master password, show account list, return (creds, vault_session, index).

    Returns (None, None, None) if the user chooses manual login or the vault
    cannot be opened.
    """
    MAX_ATTEMPTS = 3
    vault_session = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        master_pw = getpass.getpass(
            f"Vault master password (attempt {attempt}/{MAX_ATTEMPTS}): "
        ).rstrip("\r\n")
        try:
            vault_session = open_vault(master_pw)
            if not vault_session.verify_password():
                raise VaultWrongPasswordError("Wrong master password.")
            break
        except VaultWrongPasswordError:
            print("Wrong master password.")
            if attempt == MAX_ATTEMPTS:
                print("\n(M) Log in manually    (Q) Quit")
                choice = read(values=["m", "M", "q", "Q"])
                if choice.lower() == "q":
                    sys.exit(0)
                return None, None, None
        except (VaultCorruptError, VaultVersionError) as exc:
            print(f"Vault error: {exc}")
            print("Falling back to manual login.")
            return None, None, None

    accounts = vault_session.list_accounts()
    if not accounts:
        print("Vault is empty — falling back to manual login.")
        enter()
        return None, None, None

    banner()
    print("Stored accounts:\n")
    for pos, (idx, label) in enumerate(accounts, start=1):
        print(f"  ({pos}) {label}")
    print(f"  (0) Log in manually\n")

    choice = read(min=0, max=len(accounts), digit=True)
    if choice == 0:
        return None, None, None

    acct_idx = accounts[choice - 1][0]
    try:
        creds = vault_session.get_credentials(acct_idx)
    except VaultWrongPasswordError:
        # Re-read the vault from disk in case in-memory state is stale, then retry.
        try:
            vault_session = open_vault(master_pw)
            creds = vault_session.get_credentials(acct_idx)
        except (VaultWrongPasswordError, VaultCorruptError, VaultVersionError):
            print("Credential decryption failed — vault may be corrupt.")
            enter()
            return None, None, None

    return creds, vault_session, acct_idx


# ---------------------------------------------------------------------------
# Module auto-start
# ---------------------------------------------------------------------------

# Longest a single auto-started module may take to hand back before the
# launcher stops waiting and moves on to the next one.
_AUTOSTART_EVENT_TIMEOUT = 30.0


def _run_autostart_child(target, session, event, stdin_fd, predetermined_input):
    """Child entry point for an auto-started module.

    Module level (not a closure) because Windows spawns rather than forks, so
    the target must be picklable and the child does not inherit the parent's
    globals — which is also why the flag is set here rather than before
    Process().
    """
    config.autostart_active = True
    target(session, event, stdin_fd, predetermined_input)


def _run_autostart_external_child(path, session, event, stdin_fd,
                                  predetermined_input):
    """Child entry point for an auto-started EXTERNAL module.

    Module level for the same reason as _run_autostart_child: Windows spawns
    rather than forks, so the target must be picklable. External modules are
    launched by file path — the module object itself is loaded from disk and
    is not importable by name in the child.
    """
    config.autostart_active = True
    _run_external_module_child(path, session, event, stdin_fd,
                               predetermined_input)


def _autostart_targets(session):
    """Return [(module_name, function, path)] for this account's auto-start
    modules. Exactly one of function/path is set: built-in modules resolve to
    a function, external ones to their file path.

    Built-in names are resolved against the menu's own action table rather
    than a second hand-maintained registry, so the two can never drift apart.
    External modules are matched on the MODULE_NAME they declare, which is
    the same name they save their settings under. Modules whose name no
    longer resolves either way are skipped.
    """
    by_name = {fn.__name__: fn for fn in _menu_actions().values()}
    try:
        external = {name: path for name, path in get_external_modules(session)}
    except Exception:
        external = {}   # a broken modules directory must not block login
    targets = []
    for module_name in list_autostart_modules(session):
        fn = by_name.get(module_name)
        if fn is not None:
            targets.append((module_name, fn, None))
        elif module_name in external:
            targets.append((module_name, None, external[module_name]))
    return targets


def _launch_autostart_modules(session, process_list, announce=True):
    """Launch every auto-start module for this account. Returns names started.

    Shared by the login path and the "start now" menu option so both behave
    identically.  Never raises: one failing module must not block login.
    """
    # A sequenceRunner run replays a fixed script of menu keystrokes; injecting
    # extra processes would not match what the script expects.
    if len(config.predetermined_input) > 0:
        return []

    running = {p["action"] for p in process_list}
    started = []
    for module_name, fn, path in _autostart_targets(session):
        if module_name in running:
            if announce:
                print(f"  {module_name} — already running, skipped")
            continue
        try:
            event = multiprocessing.Event()
            # Empty predetermined_input in both cases: an auto-started module
            # must never consume input recorded for the interactive menu.
            if path is not None:
                target = _run_autostart_external_child
                args = (path, session, event, sys.stdin.fileno(), [])
            else:
                target = _run_autostart_child
                args = (fn, session, event, sys.stdin.fileno(), [])
            process = multiprocessing.Process(
                target=target,
                args=args,
                name=module_name,
            )
            process.start()
            process_list.append({
                "pid": process.pid,
                "action": module_name,
                "date": time.time(),
                "status": "started",
            })
            # Bounded wait: a module that wrongly prompts under auto-start
            # would never set the event, and an unbounded wait would hang the
            # login. Give up waiting and carry on — the child is left running,
            # but reaching the menu always wins.
            waited = 0.0
            while not event.wait(timeout=2):
                waited += 2
                if not process.is_alive() or waited >= _AUTOSTART_EVENT_TIMEOUT:
                    break
            started.append(module_name)
            if announce:
                if waited >= _AUTOSTART_EVENT_TIMEOUT:
                    print(f"  {module_name} — started (still initialising)")
                else:
                    print(f"  {module_name} — started")
        except Exception as exc:
            if announce:
                print(f"  {module_name} — failed to start ({exc})")

    if started:
        updateProcessList(session, programprocesslist=process_list)
    return started


def _autostart_menu(session):
    """Review and toggle which modules start automatically at login."""
    while True:
        banner()
        print("Auto-start modules")
        print("")
        print("Modules with saved settings can start automatically at login,")
        print("replaying those settings without asking anything.")
        print("")

        saved = list_saved_modules(session)
        if not saved:
            print("No modules have saved settings for this account yet.")
            print("Configure a module once, and it will appear here.")
            enter()
            return

        for pos, module_name in enumerate(saved, start=1):
            state = "ON " if is_autostart(session, module_name) else "off"
            print(f"  ({pos}) [{state}] {module_name}")
        print("")
        print("  (0) Back")
        print("Select a module to toggle.")

        choice = read(min=0, max=len(saved), digit=True)
        if choice == 0:
            return
        module_name = saved[choice - 1]
        now_on = not is_autostart(session, module_name)
        if now_on:
            print(f"\n'{module_name}' will start automatically at every login")
            print("for this account, using its saved settings.")
            confirm = read(values=["y", "Y", "n", "N", ""], empty=True,
                           default="n", msg="\nEnable? [y/N]: ")
            if confirm.lower() != "y":
                continue
        set_autostart(session, module_name, now_on)


def _prompt_region(current_locale=None, current_timezone=None):
    """Show the region preset picker. Returns (locale, timezone_id) or None.

    None means "leave unchanged / use the global default". Regions are picked
    as whole presets so the locale and timezone can never disagree.
    """
    current = config.region_label(
        current_locale or config.IKABOT_LOCALE,
        current_timezone or config.IKABOT_TIMEZONE_ID,
    )
    print("\nAccount region — sets the language, locale and timezone that this")
    print("account presents when logging in. Pick the one matching the server")
    print("you play on.")
    print(f"\nCurrent: {current}")
    print("\n  (0) Leave unchanged")
    for pos, (name, loc, tz) in enumerate(config.REGION_PRESETS, start=1):
        print(f"  ({pos}) {name}  [{loc}, {tz}]")
    choice = read(min=0, max=len(config.REGION_PRESETS), digit=True)
    if choice == 0:
        return None
    _, loc, tz = config.REGION_PRESETS[choice - 1]
    return loc, tz


def _offer_save_to_vault(session):
    """After a manual login, offer to save credentials + tokens to the vault."""
    if not session.padre:
        return

    print("\nWould you like to save these credentials to the vault for auto-login? [y/N]")
    choice = read(values=["y", "Y", "n", "N", ""], empty=True, default="n")
    if choice.lower() != "y":
        return

    if not vault_exists():
        print("\nCreating a new vault. Choose a master password.")
        print("This password protects all stored accounts — do not forget it.\n")
        master_pw = getpass.getpass("New master password: ").rstrip("\r\n")
        confirm_pw = getpass.getpass("Confirm master password: ").rstrip("\r\n")
        if master_pw != confirm_pw:
            print("Passwords do not match. Vault not created.")
            enter()
            return
        try:
            vault_session = create_vault(master_pw)
        except Exception as exc:
            print(f"Failed to create vault: {exc}")
            enter()
            return
    else:
        master_pw = getpass.getpass("Vault master password: ").rstrip("\r\n")
        try:
            vault_session = open_vault(master_pw)
        except VaultWrongPasswordError:
            print("Wrong master password. Credentials not saved.")
            enter()
            return
        except (VaultCorruptError, VaultVersionError) as exc:
            print(f"Vault error: {exc}. Credentials not saved.")
            enter()
            return

    default_label = f"{session.username} - {session.servidor} {session.mundo}"
    label_input = read(empty=True, default=default_label,
                       msg=f"Account label (default: '{default_label}'): ")
    label = label_input if label_input else default_label

    # The session just logged in successfully with its current region, so store
    # that as the account's region rather than re-prompting for one.
    region = _prompt_region(session.locale, session.timezone_id)
    if region is None:
        acct_locale, acct_timezone = session.locale, session.timezone_id
    else:
        acct_locale, acct_timezone = region

    vault_session.add_account(
        label,
        session.mail,
        session.password,
        # A region other than the one this session used invalidates the token.
        blackbox=(session.current_blackbox
                  if acct_locale == session.locale
                  and acct_timezone == session.timezone_id else None),
        lobby_token=session.current_lobby_token,
        locale=acct_locale,
        timezone_id=acct_timezone,
    )
    print(f"\nCredentials saved to vault as '{label}'.")
    enter()


def _manage_vault_menu(session):
    """Settings sub-menu for vault management."""
    while True:
        banner()
        print("Credential Vault")
        print("(0) Back")
        print("(1) List stored accounts")
        print("(2) Add current account to vault")
        print("(3) Remove an account from vault")
        print("(4) Change master password")
        print("(5) Rename an account")
        print("(6) Change vault location")
        print("(7) Change an account's region")

        choice = read(min=0, max=7, digit=True)
        if choice == 0:
            return
        elif choice == 1:
            _vault_list_accounts()
        elif choice == 2:
            _offer_save_to_vault(session)
        elif choice == 3:
            _vault_remove_account()
        elif choice == 4:
            _vault_change_master_password()
        elif choice == 5:
            _vault_rename_account()
        elif choice == 6:
            _vault_change_location()
        elif choice == 7:
            _vault_change_region()


def _vault_list_accounts():
    if not vault_exists():
        print("No vault found.")
        enter()
        return
    master_pw = getpass.getpass("Master password: ").rstrip("\r\n")
    try:
        vs = open_vault(master_pw)
    except (VaultWrongPasswordError, VaultCorruptError, VaultVersionError) as exc:
        print(f"Could not open vault: {exc}")
        enter()
        return
    accounts = vs.list_accounts()
    if not accounts:
        print("Vault is empty.")
    else:
        print()
        for pos, (idx, label) in enumerate(accounts, start=1):
            print(f"  ({pos}) {label}")
    enter()


def _vault_remove_account():
    if not vault_exists():
        print("No vault found.")
        enter()
        return
    master_pw = getpass.getpass("Master password: ").rstrip("\r\n")
    try:
        vs = open_vault(master_pw)
    except (VaultWrongPasswordError, VaultCorruptError, VaultVersionError) as exc:
        print(f"Could not open vault: {exc}")
        enter()
        return
    accounts = vs.list_accounts()
    if not accounts:
        print("Vault is empty.")
        enter()
        return
    print("\nSelect account to remove:")
    print("  (0) Cancel")
    for pos, (idx, label) in enumerate(accounts, start=1):
        print(f"  ({pos}) {label}")
    choice = read(min=0, max=len(accounts), digit=True)
    if choice == 0:
        return
    vs.remove_account(accounts[choice - 1][0])
    print("Account removed.")
    enter()


def _vault_rename_account():
    if not vault_exists():
        print("No vault found.")
        enter()
        return
    master_pw = getpass.getpass("Master password: ").rstrip("\r\n")
    try:
        vs = open_vault(master_pw)
    except (VaultWrongPasswordError, VaultCorruptError, VaultVersionError) as exc:
        print(f"Could not open vault: {exc}")
        enter()
        return
    accounts = vs.list_accounts()
    if not accounts:
        print("Vault is empty.")
        enter()
        return
    print("\nSelect account to rename:")
    print("  (0) Cancel")
    for pos, (idx, label) in enumerate(accounts, start=1):
        print(f"  ({pos}) {label}")
    choice = read(min=0, max=len(accounts), digit=True)
    if choice == 0:
        return
    current_label = accounts[choice - 1][1]
    new_label = read(msg=f"New name (current: '{current_label}'): ").strip()
    if not new_label:
        print("Name unchanged.")
        enter()
        return
    vs.rename_account(accounts[choice - 1][0], new_label)
    print(f"Account renamed to '{new_label}'.")
    enter()


def _vault_change_region():
    if not vault_exists():
        print("No vault found.")
        enter()
        return
    master_pw = getpass.getpass("Master password: ").rstrip("\r\n")
    try:
        vs = open_vault(master_pw)
    except (VaultWrongPasswordError, VaultCorruptError, VaultVersionError) as exc:
        print(f"Could not open vault: {exc}")
        enter()
        return
    accounts = vs.list_accounts()
    if not accounts:
        print("Vault is empty.")
        enter()
        return
    print("\nSelect account:")
    print("  (0) Cancel")
    for pos, (idx, label) in enumerate(accounts, start=1):
        loc, tz = vs.get_region(idx)
        if loc is None and tz is None:
            region = "default"
        else:
            region = config.region_label(loc or config.IKABOT_LOCALE,
                                         tz or config.IKABOT_TIMEZONE_ID)
        print(f"  ({pos}) {label}  —  {region}")
    choice = read(min=0, max=len(accounts), digit=True)
    if choice == 0:
        return

    acct_idx, acct_label = accounts[choice - 1]
    cur_locale, cur_timezone = vs.get_region(acct_idx)
    region = _prompt_region(cur_locale, cur_timezone)
    if region is None:
        print("Region unchanged.")
        enter()
        return
    new_locale, new_timezone = region
    if new_locale == cur_locale and new_timezone == cur_timezone:
        print("Region unchanged.")
        enter()
        return

    print(f"\nChange '{acct_label}' to "
          f"{config.region_label(new_locale, new_timezone)}?")
    print("\nThis changes the fingerprint the account presents to Gameforge,")
    print("so the next login will look like a new browser and may ask for a")
    print("captcha or an email confirmation. The stored blackbox token will be")
    print("discarded and regenerated for the new region.")
    confirm = read(values=["y", "Y", "n", "N", ""], empty=True, default="n",
                   msg="\nApply? [y/N]: ")
    if confirm.lower() != "y":
        print("Region unchanged.")
        enter()
        return

    vs.set_region(acct_idx, new_locale, new_timezone)
    print(f"\n'{acct_label}' is now "
          f"{config.region_label(new_locale, new_timezone)}.")
    enter()


def _vault_change_master_password():
    if not vault_exists():
        print("No vault found.")
        enter()
        return
    old_pw = getpass.getpass("Current master password: ").rstrip("\r\n")
    try:
        vs = open_vault(old_pw)
    except VaultWrongPasswordError:
        print("Wrong master password.")
        enter()
        return
    except (VaultCorruptError, VaultVersionError) as exc:
        print(f"Vault error: {exc}")
        enter()
        return
    new_pw = getpass.getpass("New master password: ").rstrip("\r\n")
    confirm_pw = getpass.getpass("Confirm new master password: ").rstrip("\r\n")
    if new_pw != confirm_pw:
        print("Passwords do not match. Password not changed.")
        enter()
        return
    vs.change_master_password(new_pw)
    print("Master password changed successfully.")
    enter()


def _vault_change_location():
    current = get_vault_location()
    print(f"\nCurrent vault location: {current}")
    print("Enter a new folder path, or leave blank to reset to the default location.")
    new_path = read(msg="New location: ").strip()
    if new_path == "":
        confirm_msg = "Reset vault to the default location?"
    else:
        confirm_msg = f"Move vault to '{new_path}'?"
    print(confirm_msg + " [y/N]")
    if read(values=["y", "Y", "n", "N", ""]) not in ("y", "Y"):
        print("Cancelled.")
        enter()
        return
    try:
        status = set_vault_location(new_path)
        print(status)
    except FileExistsError as exc:
        print(f"Aborted: {exc}")
    except OSError as exc:
        print(f"Failed to move vault: {exc}")
    enter()


def start():
    init()
    config.has_params = len(sys.argv) > 1
    for arg in sys.argv:
        try:
            config.predetermined_input.append(int(arg))
        except ValueError:
            config.predetermined_input.append(arg)
    config.predetermined_input.pop(0)

    creds, vault_session, acct_idx = None, None, None
    if vault_exists():
        creds, vault_session, acct_idx = _prompt_vault_login()

    if creds is not None:
        session = Session(
            mail=creds["email"],
            password=creds["password"],
            blackbox=creds.get("blackbox"),
            lobby_token=creds.get("lobby_token"),
            locale=creds.get("locale"),
            timezone_id=creds.get("timezone_id"),
        )
        # Refresh stored tokens with the ones actually used / generated during login.
        if vault_session is not None:
            vault_session.update_tokens(
                acct_idx,
                blackbox=session.current_blackbox,
                lobby_token=session.current_lobby_token,
            )
    else:
        session = Session()
        _offer_save_to_vault(session)

    if not notificationDataIsValid(session):
        banner()
        print("No notification backend is configured.")
        print("ikabot can alert you via Telegram, Discord, or ntfy.sh when")
        print("tasks complete, attacks are detected, or other events occur.\n")
        choice = read(values=["y", "Y", "n", "N", ""], empty=True, default="n",
                      msg="Set up notifications now? [y/N]: ")
        if choice.lower() == "y":
            from ikabot.function.notificationSetup import _notification_menu
            _notification_menu(session)

    # Start this account's auto-start modules before handing over to the menu.
    try:
        if _autostart_targets(session):
            banner()
            print("Starting auto-start modules...\n")
            started = _launch_autostart_modules(session, updateProcessList(session))
            if started:
                print(f"\n{len(started)} module(s) running in the background.")
            time.sleep(1.2)
    except Exception:
        # Auto-start is a convenience; never let it prevent reaching the menu.
        pass

    try:
        menu(session)
        clear()
    except KeyboardInterrupt:
        clear()
        raise
    finally:
        session.logout()


def main():
    manager = multiprocessing.Manager()
    predetermined_input = manager.list()
    config.predetermined_input = predetermined_input
    try:
        start()
    except KeyboardInterrupt:
        clear()


if __name__ == "__main__":
    # On Windows calling this function is necessary.
    if sys.platform.startswith("win"):
        multiprocessing.freeze_support()
    main()

#############################################################
# This is necessary to ensure that flask is frozen together #
# with other requirements when creating ikabot.exe          #
try: import flask                                           #
except: pass                                                #
#############################################################
