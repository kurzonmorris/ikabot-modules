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
from ikabot.function.Research import research
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
from ikabot.function.update import update
from ikabot.function.vacationMode import vacationMode
from ikabot.function.webServer import webServer
from ikabot.function.loadCustomModule import loadCustomModule
from ikabot.function.activateShrine import activateShrine
from ikabot.helpers.botComm import telegramDataIsValid, notificationDataIsValid
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import read
from ikabot.helpers.process import updateProcessList
from ikabot.web.session import *
from ikabot.function.UpgradeUnits import UpgradeUnits
from ikabot.function.modifyProduction import modifyProduction
from ikabot.function.developer import developer
from ikabot.helpers.pluginLoader import discover_plugins
from ikabot.helpers.credentialStore import (
    vault_exists, create_vault, open_vault,
    VaultWrongPasswordError, VaultCorruptError, VaultVersionError,
)


def menu(session, checkUpdate=True):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    checkUpdate : bool
    """
    menu_actions = {
        1: constructionList,
        2: sendResources,
        3: distributeResources,
        4: getStatus,
        5: activateShrine,
        6: loginDaily,
        701: alertAttacks,
        702: alertLowWine,
        801: buyResources,
        802: sellResources,
        901: donate,
        902: donationBot,
        10: vacationMode,
        11: activateMiracle,
        1201: trainArmy,
        1202: stationArmy,
        1203: UpgradeUnits,
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
        2101: proxyConf,
        2102: notificationSetup,
        2103: killTasks,
        2104: decaptchaConf,
        2105: logs,
        2106: importExportCookie,
        2107: loadCustomModule,
        2108: developer,
        22: consolidateResources,
        23: modifyProduction,
    }

    while True:
        if checkUpdate:
            checkForUpdate()
            checkUpdate = False

        show_proxy(session)
        banner()

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
        print("(23) Set Production of Saw mill / Luxury good")

        plugins = discover_plugins()
        if plugins:
            print("(24) Plugins")

        print("(30) External Modules")

        top_max = 30
        selected = read(min=0, max=top_max, digit=True, empty=True)

        if selected == '':
            continue

        if selected == 7:
            banner()
            print("(0) Back")
            print("(1) Alert attacks")
            print("(2) Alert wine running out")
            selected = read(min=0, max=2, digit=True)
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
            selected = read(min=0, max=3, digit=True)
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
            selected = read(min=0, max=2, digit=True)
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
            selected = read(min=0, max=9, digit=True)
            if selected == 0:
                continue
            if selected == 9:
                _manage_vault_menu(session)
                continue
            selected += 2100

        if selected == 30:
            while True:
                banner()
                global_dir, account_dir = get_dirs(session)
                modules = get_external_modules(session)

                print("  External Modules")
                print("  ================\n")
                if global_dir:
                    print(f"  Global:  {global_dir}")
                if account_dir:
                    print(f"  Account: {account_dir}")
                if global_dir or account_dir:
                    print()

                print("  (0) Back")
                for i, (name, _path) in enumerate(modules):
                    print(f"  ({i + 31}) {name}")
                print("  (99) Configure directories")
                print("  (100) Refresh")

                valid_choices = {0, 99, 100} | set(range(31, 31 + len(modules)))
                while True:
                    sub = read(min=0, max=100, digit=True)
                    if sub in valid_choices:
                        break

                if sub == 0:
                    break
                elif sub == 99:
                    configure_directories(session)
                elif sub == 100:
                    continue
                else:
                    mod_name, mod_path = modules[sub - 31]
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
                    event.wait()
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
            event.wait()
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
            event.wait()
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

    vault_session.add_account(
        label,
        session.mail,
        session.password,
        blackbox=session.current_blackbox,
        lobby_token=session.current_lobby_token,
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

        choice = read(min=0, max=5, digit=True)
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
