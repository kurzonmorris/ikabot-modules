# Messaging Hub — data needed to finish classification (Phase 2)

Paste the block below into the Claude browser extension while logged in to
Ikariam. Everything it asks for is read-only — no clicking, no sending, nothing
is changed in game.

## Why this is needed

The hub already knows a message is *from a player* (`<tr id="message123">`) or
*from the game* (`<tr id="gmessage123">`), and combat reports come from their own
page. What it cannot yet do reliably is split the game-generated ones into
construction / shipments / piracy / news / treaties / research, because that
split currently rests on English keyword guesses. Ikariam almost certainly marks
each row with a CSS class naming its type — that class is the reliable signal,
and I need to see real ones to build the table.

---

## The block to paste into the extension

> I'm on Ikariam (a browser game by Gameforge). I need you to collect some
> read-only page data for me. Do not click anything, do not send any message,
> do not change any setting — only read what is already on screen.
>
> **1. Tell me the interface language** of the account (English, German, …) and
> the server it is on (e.g. `s70-en`).
>
> **2. Open the message inbox** — the envelope icon, or navigate to
> `index.php?view=diplomacyAdvisor`. Make sure the list shows a good spread of
> messages: ideally at least one of each of building finished, a transport
> arriving from one of my own cities, a delivery or trade from another player,
> a pirate raid result, an espionage report, a game news or event announcement,
> a cultural treaty request, and a research completion.
>
> **3. For every row in the message list**, give me:
>    - the full `id` attribute (e.g. `message123`, `gmessage456`)
>    - the **complete `class` attribute** of the `<tr>`, and of any `<td>`,
>      `<span>` or `<img>` inside it — the class names are the important part,
>      please do not summarise or trim them
>    - the `src` and `title` of any `<img>` in the row
>    - the subject text exactly as shown, in the game's own language
>    - the sender column text
>
> **4. Expand a few of the game-generated messages** (the `gmessage` ones) and
> give me the body text of each, again exactly as written.
>
> **5. Open the combat reports page** — `index.php?view=militaryAdvisorCombatList`
> — and give me the same thing for a few rows there: full `id` and `class`
> attributes, and the visible column text.
>
> Raw HTML for a handful of representative rows is more useful to me than a
> tidy summary. If it is easier, run this in the browser console and give me
> what it prints:
>
> ```js
> copy([...document.querySelectorAll(
>   'tr[id^=message], tr[id^=gmessage], tr[id^=tbl_mail], tr[id^=tbl_gmail]'
> )].map(r => r.outerHTML).join('\n\n'));
> ```
>
> You can replace other players' names with `PLAYER1`, `PLAYER2` etc. if you
> prefer — I only need the structure, the class names and the wording. Please
> do **not** include anything from the account settings, the vault, or any
> password or token.

---

## What I do with it

- Map each system message type to its CSS class → classification stops
  depending on wording at all, and works in every language.
- Build the keyword table for this account's language as a fallback for
  servers that do not carry the classes.
- Turn the real rows into test fixtures, so a future change that breaks
  parsing fails the test suite instead of quietly misfiling your mail.

## Also useful, any time

`(6) Diagnostics → (3) Capture raw message data` inside the hub writes the same
material to
`<storage>/messaging_hub/capture/{account}_capture_*.txt`, already redacted.
Sending one of those is equivalent to the above and needs no browser work.
