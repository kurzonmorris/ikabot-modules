# Vinted ↔ Shopify Sync

A Chrome extension (Manifest V3) that keeps a Vinted wardrobe and a Shopify
clothing store in parity — stock, price, and listing content — built around
garments rather than generic products.

**Status: phase 1.** The extension installs, configures, connects, reads both
catalogues, and reports exactly what is out of parity. Writes to Shopify are
implemented; writes to Vinted are not (see [Roadmap](#roadmap)). It ships with
**dry run on by default**, so nothing is written until you turn that off.

---

## Install (unpacked)

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. **Load unpacked** → select this `vinted-shopify-sync` folder.
4. The options page opens on first install.

There is no build step — Chrome loads the folder as it is.

## Configure

### Shopify

1. In your store admin: **Settings → Apps and sales channels → Develop apps →
   Create an app**.
2. Give it these Admin API scopes: `read_products`, `write_products`,
   `read_inventory`, `write_inventory`.
3. Install the app and copy the **Admin API access token** (`shpat_…`).
4. Paste it into the extension's options along with your `*.myshopify.com`
   domain, then hit **Test connection**. The first successful connect fills in
   your inventory location automatically.

### Vinted

Vinted has no partner API, so the extension reads through a Vinted tab you are
already signed in to — it uses that session and never sees your password. Sign
in to Vinted in a normal tab, then press **Test connection**.

## Matching: the one thing to get right

Parity depends on knowing that *this* Vinted listing is *that* Shopify variant.

- Shopify variants already have a **SKU** field.
- Vinted listings have no SKU field, so put the same code in the **listing
  description**: `SKU: DRS-001` or `[DRS-001]`.

Anything without a SKU falls back to matching on **title + size**, which is good
enough to report on but is deliberately never used to drive a write.

## Running a sync

Open the popup and press **Run sync**. With dry run on you get a full plan —
what would change, on which side, and why — plus a per-item activity log. Turn
dry run off only once the plan looks right.

Directions are per field group, set in options:

| Field group | Default | Why |
|---|---|---|
| Stock & sold status | Vinted → Shopify | A garment usually sells on Vinted first |
| Price | Shopify → Vinted | The store is normally the pricing source of truth |
| Title, description, attributes | Shopify → Vinted | Store copy tends to be the maintained one |

Each can be reversed or switched off independently.

## Monitoring the process

Everything a run does is logged: fetch counts, every planned action with its
before/after values, and every failure with the platform's own error message.

- **Popup → Activity** shows the live tail.
- **Export report** writes a self-contained JSON file: settings (token
  redacted), run state, the last run's full action list, the SKU link table, and
  the log buffer.

That export is the handover point if you want an external monitor watching runs
— it needs no access to the extension's internals.

## Architecture

```
manifest.json               MV3 manifest, permissions scoped to Vinted + *.myshopify.com
src/background/
  service-worker.js         message router, scheduling, run state
src/content/
  vinted.js                 runs in the signed-in Vinted tab; reads the wardrobe
src/adapters/
  shopify.js                Admin GraphQL: read products/variants, write price/stock/content
  vinted.js                 drives the content script; writes stubbed for phase 2
src/lib/
  item.js                   the normalised garment + size/price normalisation + hashing
  diff.js                   pairs both catalogues and builds the action plan (pure)
  sync-engine.js            runs a plan, applies or dry-runs it, records the outcome
  storage.js                settings + SKU link table
  logger.js                 ring-buffer activity log
src/popup, src/options      UI
test/diff.test.mjs          tests for the pairing/diff logic
```

The adapters are the only platform-aware code. `diff.js` is pure and has no
extension dependencies, which is why the matching rules are directly testable:

```
cd vinted-shopify-sync && npm test     # 10 tests, no install required
```

### Why not the generic tool

Generic multichannel sync treats a garment as a product with arbitrary options.
Here, `size`, `brand`, `colour` and `condition` are first-class fields with
their own normalisation — `UK 10`, `Size 10` and `10` are the same size, so they
never show up as a false difference — and a Vinted sale is understood as
"single item, now gone" rather than a stock decrement.

## Security

- The Shopify Admin token is store-owner-grade. It lives in
  `chrome.storage.local` on this machine, is never synced to your Google
  account, and is redacted from exported reports. Anyone with access to your
  Chrome profile can read it — treat it like a password, and revoke the custom
  app if the machine is shared or lost.
- Host permissions are limited to Vinted domains and `*.myshopify.com`.
- The extension makes no requests to any third-party server. There is no
  telemetry.

## Roadmap

**Phase 1 — done.** Extension scaffold, settings, both connections, catalogue
reads, matching, diff engine, dry-run planning, activity log, report export,
Shopify writes.

**Phase 2 — live site work.** This is the part that has to be built against the
real sites rather than guessed at:

1. Confirm the Vinted read path against a live wardrobe. The content script
   tries the site's JSON endpoint first and falls back to reading the rendered
   grid; each run records which path produced the data (`via`), so a drift in
   Vinted's internals shows up in the log rather than silently returning
   nothing.
2. Vinted writes — price, hide/delete, description — by driving Vinted's own
   edit form in the tab.
3. Creating a listing from scratch on either side, including image transfer.
4. Size/colour writes on Shopify variant options.

**Phase 3.** Rate-limit backoff and resume, conflict handling when both sides
changed since the last snapshot, and bulk first-run SKU assignment for a
wardrobe that was never SKU'd.

## Note on Vinted's terms

Vinted's terms restrict automated access. The extension is built to act only as
the signed-in user, at human scale, on that user's own wardrobe — no anonymous
scraping, no other members' data, no background traffic without a session. Keep
automatic sync intervals conservative; account risk is yours.
