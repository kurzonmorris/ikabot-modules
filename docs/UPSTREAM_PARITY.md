# Upstream parity log

Tracks which upstream `ikabot-collective/ikabot` PRs are in this fork, and
**how** each one landed — because we frequently adapt rather than copy, and
without that record the next audit re-does work or "fixes" a deliberate
deviation back into a bug.

| | |
|---|---|
| **Parity point** | upstream **v7.4.5** |
| **Fork version** | `IKABOT_MOD_VERSION` **1.7.5** |
| **Last audited** | 2026-07-31 (commit `ea91dd2`) |
| **Scope** | `ikabot/` core only — `modules/` is fork-specific, no upstream counterpart |

> Everything at or below upstream **7.4.0** is the fork's base and is assumed
> present. This log starts from the 7.4.0 → 7.4.5 audit.

---

## 1. Status table

Legend: **Ported** = upstream code brought in · **Equivalent** = we solve it
our own way, deliberately · **Present** = already in the fork before the audit

| PR | Title | Status | Landed |
|---|---|---|---|
| #380 | Marketplace offers: locale thousand separators | Present | pre-audit |
| #406 | Improve 404 error handling in session.py | **Ported** | 1.7.5 |
| #408 | Fix local network IP resolution (Windows/Linux) | **Ported** | 1.7.5 |
| #409 | Fix menu options | **Equivalent** | pre-audit |
| #410 | Remove non-Chromium UAs; params for blackbox request | **Equivalent** | 1.7.3 |
| #411 | Improve manual blackbox login fallback | **Ported** | 1.7.5 |
| #413 | Automatic collection of ambrosia bonus | Present | pre-audit |
| #414 | Align blackbox token requests with login context | **Equivalent** | 1.7.3 |
| #416 | Add regional context support for blackbox tokens | **Equivalent** | 1.7.3 |
| #417 | Prompt manual blackbox payload before cookie fallback | Present | pre-audit |
| #418 | Separate API user-agent from manual payload context | **Ported** | 1.7.5 |
| #419 | Improve lobby fallback flow for blackbox failures | Present | pre-audit |

---

## 2. Implementation notes

Only PRs where our code differs from upstream, or where the implementation is
worth knowing before touching that area.

### #406 — 404 handling  *(ported, matches upstream)*

`ikabot/web/session.py`, in both `get()` and `post()`.

Before: **any** 404 raised `AssertionError("Session likely expired")`, so a
404 from the local web server or ikaEasy forced a full re-login. Repeated
re-logins are exactly the churn that attracts anti-bot attention.

After: only 404s where `self.host in url` count as session death — and in
`get()`, only when `index.php` is in the URL. Everything else logs a warning
and returns normally.

⚠️ **The return type matters.** `get()` must return
`response if fullResponse else html`; `post()` must return
`response if fullResponse else resp`. Returning a bare `response` breaks every
caller that expects HTML. This was got wrong once during the port and caught
by diffing against upstream source.

### #408 — local network IP  *(ported, replaced a weaker fork version)*

`ikabot/function/webServer.py`, extracted as `get_local_network_ip()`.

The fork already had a partial port using the UDP-routing trick, but probed
**`192.168.0.1`** — a private address. On any machine with a route to that
subnet (VPN, VM host-only adapter, second NIC) the kernel resolves it via
*that* interface, yielding an IP other devices cannot reach. It also had no
result validation.

Now probes **`1.1.1.1`** so the route resolves via the default gateway, checks
the result against `0.` / `127.` / `169.254.` prefixes, and falls back to a
hostname lookup. No packet is ever sent — `connect()` on a UDP socket only
asks the OS to pick a route.

### #409 — menu options  *(equivalent — do NOT "fix" to match upstream)*

Upstream validates the selection and then calls `menu()` **recursively** on
invalid input, which grows the stack every time a user mistypes.

Ours is a `while True` loop that `continue`s (`command_line.py`, in `menu()`).
Same behaviour, no unbounded recursion. **Deliberate. Leave it.**

### #410 — user agents  *(equivalent, superset)*

Upstream removed non-Chromium user agents. We went further, because the fork's
pool was independently broken: **17 of 24 strings were truncated**
(`Safari/537.3`, `Firefox/124.`, `like Geck`) — a trivially detectable bot
signature — and 5 were non-Chromium.

Replaced wholesale with 15 complete Chromium UAs (`config.py`). Note the pool
size changed, and the agent is picked with
`sum(ord(c) for c in mail) % len(user_agents)`, so **every account drew a
different UA** at the 1.7.3 upgrade.

### #414 / #416 — regional context  *(equivalent, independently implemented)*

Implemented before this audit, from the PR descriptions rather than the code.
The design **converged on upstream's almost exactly**: `self.locale`,
`self.gf_lang`, `self.accept_language`, `self.timezone_id`,
`config.build_accept_language()`, `IKABOT_LOCALE` / `IKABOT_GF_LANG` /
`IKABOT_TIMEZONE_ID`, and the hub referer built with
`self.locale.replace('-', '_')`.

Fork extras on top, which upstream does **not** have:
- `config.REGION_PRESETS` + `region_label()` — whole-region presets so a
  locale and timezone can never disagree.
- **Per-account regions in the credential vault** (`get_region` / `set_region`).
  `set_region()` clears the account's cached blackbox token, because a token
  minted under the old region contradicts the new one.
- `gf_lang` is always **derived** from the locale, never stored separately.

Also fixed in passing: 13 headers sent `en-US` while the payload declared
`en-GB`, one header sent Greek (`el`), and two lobby referers pointed at
`/es_AR/hub`.

### #411 — manual blackbox payload  *(ported, adapted)*

`__set_manual_blackbox_token()` and `__ask_manual_blackbox_payload()` in
`session.py`. Accepts the generator page's full JSON as well as a raw token,
adopting the `user_agent`, `locale` and `timezone_id` the token was minted
under — the same consistency #414/#416 exist to enforce.

Adaptations:
- An explicit environment override (`IKABOT_LOCALE` etc.) **always wins**, so
  a pasted payload cannot silently move a user off a pinned region.
- Replaced the fork's two weaker inline prompts. The one inside the login
  retry also refreshes `data["locale"]`, `data["gfLang"]` and the
  `User-Agent` / `Accept-Language` headers before retrying, since a JSON
  payload may have changed them.
- Malformed JSON falls back to treating the input as a raw token.

### #418 — API user agent  *(ported)*

`self.api_user_agent`, set alongside `self.user_agent` at login and preferred
by `getNewBlackBoxToken()`. Keeps a manual payload from desyncing later token
requests. Restored onto `self.user_agent` after a successful mint.

### #417 / #419 — lobby fallback  *(present, then upgraded)*

The fork already prompted for a manual token before falling back to the
`gf-token-production` cookie. The #411 work replaced that prompt with the
JSON-capable one.

⚠️ **`__load_new_blackbox_token()` has a different signature from upstream.**
Ours takes `stored_blackbox` (the vault fast-path); upstream takes
`allow_lobby_cookie_fallback` and returns a bool. **Do not blindly replace
this function** — the vault fast-path is fork-only and would be lost.

### Extras taken from upstream `apiComm.py`

Not part of any single PR, but latent bugs fixed while comparing:
- Response envelope is checked with `isinstance(response, dict)`. The old
  `"status" in response` did a **substring match** when the response was the
  token string.
- `return "tra:" + response.replace("tra:", "")` — an already-prefixed token
  could otherwise become `tra:tra:`.

We **keep** an extra `"Unsupported user_agent"` retry that upstream lacks.
Harmless and defensive; leave it.

---

## 3. Fork-only features that must survive future ports

If a port would remove or bypass any of these, stop and flag it:

- **Credential vault** (`helpers/credentialStore.py`) — encrypted accounts,
  per-account blackbox/lobby tokens, per-account regions.
- **`Session.__init__` extra kwargs** — `blackbox`, `lobby_token`, `locale`,
  `timezone_id`.
- **`__load_new_blackbox_token(stored_blackbox=...)`** vault fast-path.
- **Module settings memory** (`helpers/modulePrefs.py`) and **auto-start**
  (`_autostart`, `config.autostart_active`) — see `AUTOSTART_BRIEF.md`.
- **`_cached_token`** actionRequest caching in `Session`.
- **`REGION_PRESETS`**, `region_label()`, `.env` support.
- **`sequenceRunner`** and the `predetermined_input` guards.
- Everything under **`modules/`** and the **`installer/`** pipeline.

---

## 4. How to run the next parity check

1. Get upstream's version:
   `curl -s https://raw.githubusercontent.com/ikabot-collective/ikabot/master/ikabot/config.py | grep IKABOT_VERSION`
2. List the gap:
   `https://github.com/ikabot-collective/ikabot/compare/v<ours>...master`
3. **Download upstream sources and diff them locally.** Do not rely on PR
   summaries — during the 7.4.5 audit two summaries were misleading
   (`#406`'s post() snippet, and `#408` being reported as absent when a weaker
   version was present under a different variable name).
   ```bash
   curl -sS -o /tmp/up_session.py \
     https://raw.githubusercontent.com/ikabot-collective/ikabot/master/ikabot/web/session.py
   ```
4. For each PR, grep our tree for the actual behaviour, not the PR title.
   A feature may be present under different names.
5. Port what's missing, **respecting section 3**.
6. Update this file: status table, notes for anything adapted, parity point,
   and the audited date/commit.
7. Bump `IKABOT_VERSION` to the upstream version **only if parity is real**,
   and bump `IKABOT_MOD_VERSION`.

---

## 5. Changelog

### 2026-07-31 — parity with 7.4.5 (`ea91dd2`, mod 1.7.5)
Audited all 12 PRs in 7.4.0 → 7.4.5. Ported #406, #408, #411, #418 plus two
`apiComm` fixes. Confirmed #380, #413, #417, #419 present; #409, #410, #414,
#416 satisfied by equivalent fork implementations. `IKABOT_VERSION` corrected
7.4.0 → 7.4.5.
