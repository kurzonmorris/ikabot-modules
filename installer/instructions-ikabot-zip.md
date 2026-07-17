# Instructions: Build Ikabot EXE Release Zip

**Trigger:** When told to "load" or "run" this file, execute the steps below exactly — no further input needed.

---

## What This Does

Packages the compiled ikabot Windows executable and its dependencies into a dated release zip and pushes it to the repo.

---

## Steps

### 1. Read version numbers from config.py
```bash
grep "IKABOT_VERSION\b" /home/user/ikabot-modules/ikabot/config.py
grep "IKABOT_MOD_VERSION\b" /home/user/ikabot-modules/ikabot/config.py
```
Extract the values of `IKABOT_VERSION` and `IKABOT_MOD_VERSION`.  
These form the zip name: `ikabot-v{IKABOT_VERSION}-mod-v{IKABOT_MOD_VERSION}.zip`  
Example: `ikabot-v7.3.3-mod-v0.9.4.zip`

### 2. Get current UK date and time
```bash
date -u '+%d-%m-%y_%H-%M'
```
*(UK time = UTC+1 BST in summer, UTC+0 GMT in winter — adjust from system UTC if needed)*

### 3. Confirm source contents
The zip source is `dist/ikabot/` in the repo root. It should contain:
- `ikabot.exe`
- `_internal/` folder

If either is missing, stop and report — do not create an empty or partial zip.

### 4. Create the zip
```bash
cd /home/user/ikabot-modules/dist/ikabot
zip -r "ikabot-v{IKABOT_VERSION}-mod-v{IKABOT_MOD_VERSION}.zip" ikabot.exe _internal/
```

### 5. Move zip to releases/
```bash
mv "ikabot-v{IKABOT_VERSION}-mod-v{IKABOT_MOD_VERSION}.zip" /home/user/ikabot-modules/releases/
```

### 6. Commit and push
Stage the new zip, commit with a message stating the ikabot and mod versions included, push to the current working branch.

---

## Expected Output

A file at:
```
releases/ikabot-v{IKABOT_VERSION}-mod-v{IKABOT_MOD_VERSION}.zip
```

Containing:
- `ikabot.exe`
- `_internal/` and all its contents
