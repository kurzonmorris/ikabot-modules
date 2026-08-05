# Messaging Hub — data needed to finish classification (Phase 2)

> **Round 1 is done** (captured 2026-08-05, English account). What it showed:
>
> - The inbox is `table01 dotted left clearfloat`, six cells per row: checkbox,
>   expand toggle, sender, `td.subject`, town, date. The hub's parser reads all
>   of that correctly, confirmed by fixtures in `tools/testMessagingHub.py`.
> - **There were no `gmessage` rows at all.** Every row was `message###`, and the
>   only content was player mail plus diplomacy treaty offers.
> - Treaty offers carry an **empty** `td.msgText`; the wording and the
>   accept/decline links live in a separate hidden `tr#tbl_reply<id>` row.
> - Unread rows carry `new` in the `<tr>` class.
> - No CSS class anywhere names the message *type* — the hoped-for
>   language-independent signal does not exist in the inbox.
> - The combat list was empty ("There are no combat reports available"), so
>   combat parsing is still unverified against real data.
>
> **What this means:** construction, shipments, piracy, espionage and news are
> almost certainly not inbox messages in current Ikariam. They live somewhere
> else, and round 2 below is about finding out where.

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

---

## Round 2 — where do the other message types actually live?

The inbox does not carry construction, shipments, piracy, espionage or news.
Before the hub can forward them I need to know which view does. Paste this into
the extension:

> I'm on Ikariam. Read-only again — do not click anything that sends, accepts,
> declines, attacks or spends. Opening a tab or panel to look at it is fine.
>
> I'm trying to find where each kind of game notification actually appears. For
> each of the following, tell me whether it exists, what the URL is, and if it
> has content give me a couple of rows of raw HTML including every `id` and
> `class` attribute:
>
> 1. **The notification / events panel** — whatever shows "building finished"
>    and similar. Where is it in the DOM, is it in the page HTML or loaded by
>    an ajax call, and what does one entry look like?
> 2. `index.php?view=militaryAdvisor` — the movements list. Rows for any
>    transport, attack or return currently in progress.
> 3. `index.php?view=militaryAdvisorReportView` and
>    `index.php?view=militaryAdvisorEspionage` if they exist — espionage
>    reports.
> 4. **The pirate fortress** — where a completed raid's result is shown.
> 5. **Building upgrades** — after a building finishes, where is that told to
>    me? A notification, a message, or only the city view changing?
> 6. **A completed transport between two of my own cities**, and a delivery
>    from another player — where does each show up?
> 7. Anything in the top bar with a counter or badge: what is it called, what
>    is its element id, and what does clicking it open?
>
> If a section is simply empty right now, say so — "this page exists and says
> there is nothing" is useful to me, it is different from "this page does not
> exist".

The most valuable single answer is **7** — the top-bar counters usually reveal
the endpoints everything else hangs off.

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
