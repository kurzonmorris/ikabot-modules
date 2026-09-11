# Driving ikabot from a script or web front-end

How to send commands to a running ikabot without knowing what screen it is on.

---

## 1. `/menu` — return to the main menu from anywhere

Type `/menu` at **any** ikabot prompt and it abandons whatever is in progress
and lands back on the main menu. It works at digit-only prompts, yes/no
prompts, city pickers, sub-menus, `[Enter]` pauses — everywhere input is read.

That is what makes it usable as a prefix: a front-end never has to know where
the session currently is.

### Why not Ctrl+C

Ctrl+C is a *terminal signal*, not a character. It cannot be written into a
pipe or a socket, so a web front-end cannot send one. And at the top level it
exits ikabot rather than returning to the menu, which is the opposite of what
you want.

`/menu` is ordinary text, so it travels anywhere stdin does.

### It can carry the command with it

Anything after the token is queued as menu input:

```
/menu 17 1
```

means "return to the main menu, then pick 17 (Auto-Pirate), then 1". One line
is a whole command, rather than a reset followed by separate keystrokes.

So a front-end can be stateless — every command it sends is self-contained:

```
/menu 4                 # account status
/menu 16                # start the web server
/menu 2101              # proxy configuration
```

Digits are queued as numbers, everything else as text, matching how
command-line arguments are handled at startup.

### What happens to a running module

If `/menu` arrives while a module is asking questions, that module's process
unwinds and exits, and the menu redraws. Tasks already running **in the
background are not affected** — they have no terminal and are not listening.
To stop those, use Kill Tasks (2103).

---

## 2. Answering the other half: telling it to "do X" without keystrokes

You can, and in more than one way.

**At startup** — arguments become the input queue:

```
ikabot 17 1
docker run ... ikabot 4
```

**At runtime** — `/menu 17 1`, as above.

**Saved settings** — most modules that ask a lot of questions can remember
their answers per account. Configure once, and afterwards the module offers
"(1) Use these settings [just press ENTER]", so the whole command collapses to
`/menu 17` + Enter. See `docs/AUTOSTART_BRIEF.md`.

**Auto-start** — a module with saved settings can be marked to launch by itself
at login, with no input at all. Menu → Auto-start.

**sequenceRunner** — the external module in `modules/` records and replays a
whole sequence of menu choices.

For a front-end, saved settings plus `/menu <number>` is usually the best
combination: the settings live in ikabot where they belong, and the front-end
only has to know one number per command.

---

## 3. Practical notes for a container

- **Line-based.** Send one line at a time, each ending in a newline.
- **`[Enter]` prompts read the terminal, not the pipe.** On Linux ikabot uses
  `getpass` for "press Enter" pauses, which reads `/dev/tty` when one exists.
  Writing to the container's stdin works if the container was started with
  `-it` and you attach to it; feeding a bare pipe can leave those prompts
  unanswered. If you hit that, `/menu` will not rescue you either — it is read
  through the same channel. Start containers with `-it`.
- **Pacing.** ikabot pops queued input as fast as it is asked for. If inputs
  land in the wrong place, set `sequence_input_delay` in `ikabot/config.py`
  (e.g. `0.3`) to slow the replay down.
- **Don't send `/menu` twice in a row quickly.** The second one clears the
  queue the first one just filled.

---

## 4. Reading state back

The front-end does not need to scrape the terminal to know what is running —
each instance writes a JSON status file. See `docs/DOCKER_STATUS_API.md`.
