# Kurzon — How I Work and What I Need

> **Purpose:** Read this file at the start of every session. It tells you how
> Kurzon works and what he expects from you. It is about Kurzon, not about one
> project. For the technical detail of the ikabot project, read
> `Explained-ikariam_ikabot.md`.

---

## 1. How to Write to Kurzon

Write in **ASD-STE100 Simplified Technical English**.

Kurzon still wants a full explanation. He wants it in words that are easy to
read. Do not remove the content. Make the words simpler.

Follow these rules:

- Write short sentences. Use 20 words or fewer for an instruction. Use 25 words
  or fewer for a description.
- Put one idea in each sentence.
- Use the active voice. Write "the module reads the file". Do not write "the
  file is read by the module".
- Use a simple verb tense: present, past, or future.
- Do not use an `-ing` verb form if a simple verb works.
- Use the same word for the same thing every time. Do not change the word for
  variety.
- Use articles. Write "the file", not "file".
- Write an instruction as a command. Write "Run the test". Do not write "The
  test should be run".
- Keep a paragraph to 6 sentences or fewer.
- Do not use slang, idioms, or jokes.
- Do not change a technical name. A function name, a parameter name, and a file
  name stay exactly as they are.

**One limit to know.** Full ASD-STE100 also has an approved word list. You do
not have that list. Obey the rules above. They give most of the benefit. Do not
claim full compliance with the standard.

---

## 2. Communication Style

- Kurzon writes short, direct requests. He does not give background unless you
  ask for it.
- A request can be as short as "increase version" or "fix this error". Find the
  correct files yourself.
- Kurzon copies log output and error text exactly as he sees it. Read it
  carefully. It is real evidence.
- "Revert" means `git revert HEAD --no-edit`.
- Tell Kurzon what you did **and** what you could not do. Do not hide a
  limitation. He will find it later, and late news is worse.
- If you are not sure, say so. A clear "I do not know yet" is better than a
  confident guess.

---

## 3. When You Need Information Kurzon Cannot Give

Kurzon has the **Claude browser extension**. It can open a live page. It can
read the page code and the network traffic.

**Use the extension. Do not guess.** A guess about a web page, a URL, or an API
is usually wrong. A capture from the live page is correct. This project lost
days to guessed URLs that the server silently refused.

**Ask for the extension yourself.** Do not wait for Kurzon to offer it. If you
need a fact that only the live site holds, say so and ask.

When you ask, give Kurzon all of this:

1. **The page.** Give the exact URL, or name the screen to open.
2. **The sign-in.** Say if he must log in first, and to which account.
3. **The steps.** Name each click in order. Name the exact button text.
4. **The data you need.** Name the field, parameter, attribute, or tag. Say if
   you need the request URL, the request parameters, or the response body.
5. **The reason.** Say in one line what the data will fix.

Also follow these rules:

- **Keep the request short.** The extension has a context limit. A long request
  fails.
- **One question at a time.** Two questions in one message often return one
  answer.
- **Tell him to start a new extension chat** if the extension reports a context
  error. An old chat holds large page captures and cannot accept more.
- **Warn him about any real change.** Some clicks change the live account and
  spend real resources. Say this before he clicks.
- **Ask for a redacted token.** Tell him to hide a session token or a password,
  but to keep the parameter name.

**Check the answer before you trust it.** A failed request can leave the old
page content on screen. That looks like a correct answer but is not. Ask him to
clear the network log between steps when this risk exists.

---

## 4. Platform and Environment

### Devices

- **Primary device:** Steam Deck with SteamOS, in desktop mode. A Steam Machine
  with the same system will join it.
- **Server:** Unraid. It ran a Windows VM. It now also runs a Linux Docker
  container. Much of the work runs in the container.
- **Both must work.** Your code must be correct on Windows **and** in the Linux
  container. Do not fix one and break the other.
- **Write instructions for Windows by default.** Use Windows paths, `.bat`
  files, and PowerShell. Use Linux, SteamOS, or Docker only when Kurzon asks.

### Docker

- The appdata volume mounts at `/config`. `HOME=/config`. A file written to `~`
  goes there and persists.
- Check with `echo $HOME` before you assume this.

### Concurrency

Kurzon runs about **24 accounts at the same time**. Always assume concurrency:

- Give every per-account file a name that includes the account.
- Do not share a mutable file between instances.
- Make a process check namespace-aware. A process id alone is not enough.

### Which system to assume

- Install or run instructions → the Windows VM.
- Server work → Unraid.
- Desktop or gaming work → SteamOS.

---

## 5. File and Version Naming

Kurzon works by version number. A version tells him what he is running and what
changed. Put a version on anything that can carry one.

### Where a version goes

- **A file.** Put the version before the extension, after `_v`:

  ```
  constructionManager_v2.3.4.py
  ```

- **Inside the file.** Keep a `__version__` constant that matches the name.
- **A banner or a title screen.** Show the version to the user. He must see the
  version without opening the file.
- **A file group.** Files that ship together share one version. Give the group
  one number. Do not let the members drift apart.

### Naming rules

- A file name must explain itself. The name alone tells you what the file does.
  Examples: `resourceTransportManager`, `constructionManager`, `tavernManager`.
- Use **camelCase** for a name with several words.
- Use `MAJOR.MINOR.PATCH`:
  - **MAJOR** — a large or breaking change.
  - **MINOR** — a new feature or a real improvement.
  - **PATCH** — a bug fix.

### Who decides a version, and who updates it

These two rules work together. Keep them apart in your mind.

**Kurzon decides the number.** Never invent a bump. Never raise a version he did
not name, and never touch a different version at the same time. Example: a
module bump does not change the mod version.

**You update every location. Do this automatically.** When a version changes,
change it everywhere in the same commit. Do not ask first. Do not leave one
place behind.

Update all of these:

1. The file name.
2. The `__version__` constant inside the file.
3. The banner or title screen.
4. Every other file in the same group.
5. Every reference in the documentation. This includes example file names and
   module tables.
6. Any installer, manifest, or script that names the file.

**Then prove it.** Search the repository for the old number before you report
the work as done:

```bash
grep -rn "v2\.3\.3" --include=*.py --include=*.md .
```

A stale version in a document is a real error. It tells Kurzon he runs an old
build. This has happened: a module reached v2.3.4 while the reference file still
said v2.2.8 in two places.

---

## 6. Git and Branches

- A branch name must show the topic. You must know the purpose without opening
  the branch.
- Put the key word in the name. Work on the tavern manager → the name holds
  `tavern-manager`.
- Use lowercase words and hyphens.
- **A name that Kurzon asks for always wins.** Use his exact name. Do not
  correct it.
- Keep using the branch until he tells you to change.
- Do not push to `main` without permission.
- Commit and push after you finish a task.
- Write what the commit fixes and why. State the evidence for the fix.

---

## 7. Code Standards

- Do not add an unnecessary comment. Comment only when the reason is not
  obvious.
- Keep a docstring to one line.
- Do not handle an impossible error.
- Do not add a feature flag or a compatibility shim.
- Make the smallest change that does the task.
- Do not refactor code that the task does not touch.
- Do not design for a future requirement that nobody asked for.
- Keep a module self-contained when the project allows it.

---

## 8. After You Write Code

Do these steps before you say the work is complete.

1. **Compile the file.**

   ```bash
   python3 -m py_compile <file>
   ```

2. **Test the behaviour, not only the syntax.** Run the new logic with real
   inputs. Test the failure cases too.
3. **Test the old code as well** when you fix a bug. Show that the old code
   fails and the new code passes. This proves the diagnosis, not only the fix.
4. **Check your own test.** A test can fail because the test is wrong. Find out
   which side is wrong before you believe the result.
5. **Check the version numbers.** If a version changed, search for the old
   number. Confirm that no file, banner, or document still holds it. See §5.
6. **Commit and push.**
7. **Report.** Say what you changed, what you tested, what you decided, and what
   is still open.

---

## 9. Projects

Kurzon works on **ikabot**, a Python automation tool for the browser game
Ikariam. He maintains a fork with extra features and a set of external modules.
`Explained-ikariam_ikabot.md` holds all the technical detail for that work.

Treat this file as general. Do not add project detail here. Put project detail
in the project's own reference file.

---

*Last updated: 2026-09-21.*
