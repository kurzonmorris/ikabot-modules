#! /usr/bin/env python3
# -*- coding: utf-8 -*-

MODULE_NAME  = "Sequence Runner"
MODULE_ENTRY = "sequenceRunner"

import json
import os
import sys

import ikabot.config as config
from ikabot.config import IKABOT_DATA_DIR
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.pedirInfo import read

_SEQUENCES_FILE = os.path.join(IKABOT_DATA_DIR, "sequences.json")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _load_sequences():
    try:
        with open(_SEQUENCES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("sequences", [])
    except (OSError, json.JSONDecodeError):
        return []


def _save_sequences(sequences):
    os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
    with open(_SEQUENCES_FILE, "w", encoding="utf-8") as f:
        json.dump({"sequences": sequences}, f, indent=2)


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

def _parse_input_string(raw):
    """Parse comma-separated user input into a typed list.

    Rules:
      'enter' or blank token -> ""   (empty = press Enter with no input)
      integer-parseable      -> int
      anything else          -> str
    """
    result = []
    for token in raw.split(","):
        token = token.strip()
        if token.lower() == "enter" or token == "":
            result.append("")
        else:
            try:
                result.append(int(token))
            except ValueError:
                result.append(token)
    return result


# ---------------------------------------------------------------------------
# Core run
# ---------------------------------------------------------------------------

def _run_sequence(seq, predetermined_input, event):
    """Inject sequence inputs into the shared list then signal done.

    The main menu loop resumes immediately after event.set() and read()
    starts consuming the injected values automatically.
    """
    predetermined_input.extend(seq["inputs"])
    event.set()


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _create_sequence(sequences):
    banner()
    print("  Create New Sequence")
    print("  " + "=" * 40 + "\n")

    name = read(msg="Sequence name: ").strip()
    if not name:
        print(f"\n{bcolors.RED}Name cannot be empty.{bcolors.ENDC}")
        enter()
        return

    description = read(msg="Description (optional, Enter to skip): ", empty=True).strip()

    print("\n  Enter inputs as comma-separated values.")
    print("  Use 'enter' for an empty/Enter keypress.")
    print("  Numbers are entered as digits, text as words.")
    print("  Example: 16, enter, 5, 1, 0, 2, 6, 1, y, enter\n")

    raw = read(msg="Inputs: ").strip()
    if not raw:
        print(f"\n{bcolors.RED}No inputs provided. Sequence not saved.{bcolors.ENDC}")
        enter()
        return

    inputs = _parse_input_string(raw)

    sequences.append({
        "name": name,
        "description": description,
        "inputs": inputs,
    })
    _save_sequences(sequences)

    print(f"\n{bcolors.GREEN}Sequence '{name}' saved with {len(inputs)} steps.{bcolors.ENDC}")
    enter()


def _delete_sequence(sequences):
    if not sequences:
        banner()
        print("  No sequences to delete.")
        enter()
        return

    banner()
    print("  Delete Sequence")
    print("  " + "=" * 40 + "\n")
    print("  (0) Cancel\n")
    for i, seq in enumerate(sequences, start=1):
        print(f"  ({i}) {seq['name']}")

    choice = read(min=0, max=len(sequences), digit=True)
    if choice == 0:
        return

    removed = sequences.pop(choice - 1)
    _save_sequences(sequences)
    print(f"\n{bcolors.GREEN}Deleted '{removed['name']}'.{bcolors.ENDC}")
    enter()


def _preview_sequence(seq):
    banner()
    print(f"  Sequence: {seq['name']}")
    print("  " + "=" * 40)
    if seq.get("description"):
        print(f"  {seq['description']}\n")
    print(f"  Steps ({len(seq['inputs'])}):\n")
    for i, val in enumerate(seq["inputs"], start=1):
        display = "[ Enter ]" if val == "" else repr(val)
        print(f"    {i:>3}. {display}")
    enter()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def sequenceRunner(session, event, stdin_fd, predetermined_input):
    if sys.platform != "win32":
        try:
            sys.stdin = open("/dev/tty", "r")
        except Exception:
            sys.stdin = os.fdopen(stdin_fd)
    else:
        sys.stdin = os.fdopen(stdin_fd)

    config.predetermined_input = predetermined_input

    try:
        while True:
            sequences = _load_sequences()
            n = len(sequences)

            banner()
            print("  Sequence Runner")
            print("  " + "=" * 40 + "\n")

            print("  (0) Back\n")

            if sequences:
                for i, seq in enumerate(sequences, start=1):
                    desc = f"  —  {seq['description']}" if seq.get("description") else ""
                    print(f"  ({i}) {seq['name']}{desc}")
                print()

            print(f"  ({n + 1}) Create new sequence")
            if sequences:
                print(f"  ({n + 2}) Delete a sequence")
                print(f"  ({n + 3}) Preview a sequence")

            max_choice = n + 3 if sequences else n + 1
            choice = read(min=0, max=max_choice, digit=True)

            if choice == 0:
                break
            elif 1 <= choice <= n:
                _run_sequence(sequences[choice - 1], predetermined_input, event)
                return   # event already set; exit child immediately
            elif choice == n + 1:
                _create_sequence(sequences)
            elif sequences and choice == n + 2:
                _delete_sequence(sequences)
            elif sequences and choice == n + 3:
                banner()
                print("  Preview which sequence?\n")
                print("  (0) Cancel")
                for i, seq in enumerate(sequences, start=1):
                    print(f"  ({i}) {seq['name']}")
                pick = read(min=0, max=n, digit=True)
                if pick != 0:
                    _preview_sequence(sequences[pick - 1])

    except KeyboardInterrupt:
        pass

    event.set()
