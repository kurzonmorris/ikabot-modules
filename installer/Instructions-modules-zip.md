# Instructions: Build Modules Release Zip

**Trigger:** When told to "load" or "run" this file, execute the steps below exactly — no further input needed.

---

## What This Does

Packages all active modules and config examples into a dated release zip and pushes it to the repo.

---

## Steps

### 1. Get current UK date and time
```bash
date -u '+%d-%m-%y_%H-%M'
```
Use the output as the timestamp in the zip filename. Format: `DD-MM-YY_HH-MM`  
*(UK time = UTC+1 BST in summer, UTC+0 GMT in winter — adjust from system UTC if needed)*

### 2. Collect files
- All files in `modules/` — **exclude any `.md` files**
- All files in `config-examples/`

### 3. Create the zip
```bash
cd /tmp
mkdir ikabot-release-staging
cp /home/user/ikabot-modules/modules/*.py ikabot-release-staging/
cp /home/user/ikabot-modules/config-examples/* ikabot-release-staging/
zip "ikabot-modules-DATE_TIME.zip" ikabot-release-staging/*
```
Replace `DATE_TIME` with the timestamp from step 1.

### 4. Move zip to releases/
```bash
mv "ikabot-modules-DATE_TIME.zip" /home/user/ikabot-modules/releases/
```

### 5. Commit and push
Stage the new zip, commit with a short message listing the included module versions, push to the current working branch.

---

## Expected Output

A file at:
```
releases/ikabot-modules-DD-MM-YY_HH-MM.zip
```

Containing:
- All `.py` files from `modules/`
- All files from `config-examples/`
- No `.md` files
