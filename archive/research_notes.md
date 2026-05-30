# Ikabot Custom Modules (Kurzon’s Pack)

A small set of custom modules for **Ikabot** (Ikariam bot) focused on **resource logistics, recruitment automation, and quality-of-life tools**.

---

## Contents

- [Resource Transport Manager](#resource-transport-manager)
- [Import/Export Cookie + Send to Telegram](#importexport-cookie--send-to-telegram)
- [Tavern Manager v0.8](#tavern-manager-v08)
- [Auto Recruitment v0.5](#auto-recruitment-v05)
- [Quick Install (Load as a Custom Module)](#quick-install-load-as-a-custom-module)
- [Permanent Install](#permanent-install)

---

## Resource Transport Manager

**File:** `resourceTransportManager.py`  
**What it does:** The ultimate resource transport manager. Handles **one-off shipments** or **fully automated, scheduled logistics**. Great for consolidating everything into one city (inside or outside your empire), distributing resources (wine/sulphur/etc.), or balancing a resource evenly across cities.

<details>
  <summary><strong>Detailed feature set (click to expand)</strong></summary>

### Consolidate / Single Shipments (Multiple cities → One destination)
Consolidate mode gathers resources from one or many source cities into a single target city, with control over how much to keep vs. send. Supports internal and external destinations, plus one-time or recurring schedules.

- Choose **Merchant ships** or **Freighters**
- Select **single or multiple** source cities
- Choose send logic:
  - **Keep mode** (keep reserve, send excess), or
  - **Specific amount mode** (send exact amounts)
- Destination can be:
  - **Internal city** (your city), or
  - **External city** via **X/Y coordinates** and city selection on that island
- Includes **Telegram notification preferences** and **schedule configuration** (one-time or recurring)

### Distribute (One city → Multiple destinations)
Distribute mode sends configured amounts from one source city to many destination cities in one workflow. Prevents invalid self-targeting and calculates totals before you confirm.

- Choose **Merchant ships** or **Freighters**
- Pick **one source city** and **multiple destination cities**
- Automatically removes the source city if selected as a destination
- Set **per-resource amount** to send to each destination (with restart support)
- Shows per-destination amounts and **grand total required**, then supports notifications + scheduling

### Even Distribution (Balance one resource across selected cities)
Balances a single selected resource across chosen cities using Ikabot’s distribution-routing logic. Previews every route before executing.

- Choose **Trade ships** (default) or **Freighters**
- Select the **resource type** to balance
- Select participating cities, then generate routes with `distribute_evenly`
- Review a **shipment preview list** before proceeding
- Executes routes directly after confirmation

</details>

---

## Import/Export Cookie + Send to Telegram

**File:** `importExportCookie.py`  
**What it does:** Sends your Ikariam **cookie** to your registered **Telegram**, useful for logging in from other PCs quickly.

⚠️ **Security warning:** If this code is made public or accessed by anyone else, it can give them **full access** to your Ikariam account (via your cookie). Use with caution.

---

## Tavern Manager v0.8

**File:** `tavernManager.py`  
**What it does:** Automatically adjusts **wine consumption** based on whether your city is full of citizens.  
If the city is full, it will reduce wine use while keeping **Satisfaction above 0**, so you **waste less wine**.

---

## Auto Recruitment v0.5

**File:** `autoRecruitment.py`  
**What it does:** Automates recruiting **troops and ships** up to a target number—recruiting as much as possible across selected cities, then repeating in waves as citizens become available until the target is reached.

Example: You set **10,000 Hoplites**, but don’t have enough citizens for all at once. It recruits what it can now, then keeps recruiting later until it hits 10,000.

---

## Quick Install (Load as a Custom Module)

This is the quickest way to run any of these modules without changing Ikabot itself.

1. Put the `.py` file in a folder anywhere you like (example: Desktop).
2. In Windows File Explorer, open that folder.
3. Click the blank area to the right of the breadcrumb path so it turns into a full path like:
   - `C:\Users\username\Desktop\ikariam`
4. Append `\` + the module filename, for example:
   - `C:\Users\username\Desktop\ikariam\resourceTransportManager.py`
5. In Ikabot:
   - Go to **(21) Options / Settings**
   - Go to **(8) Load custom ikabot module**
   - Choose **(1) Add new module**
6. Paste the **full file path** and confirm.

**To run later:**  
From the main menu: **21 → 8 → select the module number** (often #2, depending on your list).

---

## Permanent Install

**Coming soon.**
