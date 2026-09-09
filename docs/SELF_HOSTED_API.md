# Self-hosted ikabot API — Unraid container + failover

## Why

ikabot cannot log in on its own. Gameforge requires a "blackbox" token that is
produced by running their JavaScript in a real browser, and ikabot outsources
that to a public API server (`ikagod.twilightparadox.com`, resolved from a DNS
TXT record). It uses the same server for pirate captchas, though this fork
solves those locally first.

That server is one volunteer-run box. When it is down, **every account stops
logging in** — around two dozen of them here, all at once. This sets up your
own copy on Unraid and teaches ikabot to fall back to it.

The public server stays primary. Yours takes over only when it has to.

---

## What gets deployed

Three containers, defined in `docker/ikabot-api/docker-compose.yml`:

| Container | Image | Job |
|---|---|---|
| `ikabot-api` | built from `Ikabot-Collective/IkabotAPI` | FastAPI + Playwright. Mints tokens, solves captchas. |
| `ikabot-api-gate` | `caddy:2-alpine` | Checks the API key, allows only the two endpoints ikabot uses. |
| `ikabot-api-tunnel` | `cloudflare/cloudflared` | Outbound-only tunnel to Cloudflare. |

```
Internet ──► Cloudflare edge ──► (outbound tunnel) ──► gate ──► ikabot-api
                                                       ▲
                              LAN / tailnet ───────────┘
```

### How this stays safe

- **No inbound port is opened.** `cloudflared` dials *out* to Cloudflare and
  the traffic comes back down that connection. Your router keeps every inbound
  port shut, and Unraid's public IP is never in DNS.
- **The API is never exposed directly.** `ikabot-api` publishes no ports at
  all. The only thing that can reach it is the gate, on an internal docker
  network — so there is no route in that skips the key.
- **A key is required on every request.** Upstream ships no authentication
  whatsoever and CORS `*`; unprotected, your box becomes a free
  token-and-captcha service for anyone who finds it.
- **Only two endpoints are reachable**, `/v1/token` and
  `/v1/decaptcha/pirate` (plus `/v1/decaptcha/lobby`). `/docs`, `/redoc` and
  the home page return 404 — an interactive API explorer is a gift to anyone
  who does get a key.
- **The blast radius is a game bot.** No game credentials, no vault, no
  session data ever reaches this container. The worst case for a leaked key is
  someone burning your Unraid CPU on Chromium.
- **Resource caps.** Each token spawns a Chromium; `mem_limit` and `cpus` stop
  a burst from paging out the rest of the server.

---

## 1. Cloudflare: create the tunnel

You need a domain on Cloudflare (any domain, on the free plan).

1. Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** →
   **Create a tunnel** → **Cloudflared**.
2. Name it `ikabot-api`. Save.
3. On the install screen, copy the long string after `--token` in the Docker
   command. That is your `TUNNEL_TOKEN`.
4. **Public Hostnames** → **Add a public hostname**:
   - Subdomain: `ikabot-api`, Domain: your domain
   - Service type: `HTTP`, URL: `ikabot-api-gate:8080`
5. Save. Cloudflare creates the DNS record for you.

Nothing is reachable yet — the tunnel has no daemon running at your end.

## 2. Unraid: bring the stack up

Install **Compose Manager** from Community Applications if you have not
already, or use the terminal. Then:

```bash
mkdir -p /boot/config/plugins/compose.manager/projects/ikabot-api
cd /boot/config/plugins/compose.manager/projects/ikabot-api

# from a clone of this repo, or copy the three files by hand
cp /mnt/user/appdata/ikabot-modules/docker/ikabot-api/docker-compose.yml .
cp /mnt/user/appdata/ikabot-modules/docker/ikabot-api/Caddyfile .
cp /mnt/user/appdata/ikabot-modules/docker/ikabot-api/SupportedUserAgents.json .
cp /mnt/user/appdata/ikabot-modules/docker/ikabot-api/.env.example .env
```

Generate a key and fill in `.env`:

```bash
openssl rand -base64 48
```

```ini
IKABOT_API_KEY=<the string you just generated>
TUNNEL_TOKEN=<the token from step 1>
```

Then build and start. The first build pulls Chromium, so give it ten minutes:

```bash
docker compose up -d --build
docker compose logs -f
```

`docker compose` refuses to start if `IKABOT_API_KEY` or `TUNNEL_TOKEN` is
empty, rather than quietly bringing up an open server.

## 3. Verify

From the Unraid terminal — the gate is on the LAN at port 5080:

```bash
# health needs no key
curl -s http://localhost:5080/health

# no key: 401
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5080/v1/token

# with key: a token, after ~10-20s
curl -s -H "X-API-Key: $IKABOT_API_KEY" \
  'http://localhost:5080/v1/token?user_agent=Mozilla/5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20(KHTML,%20like%20Gecko)%20Chrome/123.0.0.0%20Safari/537.36&locale=en-GB&timezone_id=Europe/London'

# the docs are not reachable: 404
curl -s -o /dev/null -w '%{http_code}\n' -H "X-API-Key: $IKABOT_API_KEY" http://localhost:5080/docs
```

Then the same through the tunnel, from anywhere:

```bash
curl -s https://ikabot-api.example.com/health
curl -s -o /dev/null -w '%{http_code}\n' https://ikabot-api.example.com/v1/token   # 401
```

A slow first token is normal — Chromium has to start.

## 4. Point ikabot at it

In the `.env` next to each ikabot install (or the container's environment):

```ini
IKABOT_API_FALLBACK=https://ikabot-api.example.com
IKABOT_API_KEY=<the same key>
```

That is the whole change. ikabot resolves the public server as before, and if
the request fails — DNS, connection refused, a 500, a timeout — it retries the
same request against your server with the key attached. The key is only ever
sent to the addresses in `IKABOT_API_FALLBACK`; the public server never sees
it.

Confirm it is loaded at **(21) Options → (2108) Developer**, which now prints
the fallback addresses, whether a key is set, and the per-server timeout.

`IKABOT_API_TIMEOUT` (default 120s) is how long one server gets before ikabot
moves on. Before this change the timeout was 900s, which meant a hung public
server blocked a login for a quarter of an hour with nothing to fail over to.

### Testing the failover for real

Point the primary at a black hole and check the fallback carries the login:

```ini
CUSTOM_API_ADDRESS=http://127.0.0.1:1
IKABOT_API_FALLBACK=https://ikabot-api.example.com
IKABOT_API_KEY=<key>
```

Log in. It should succeed, and `~/.ikabot/logs/` will show the primary failing
followed by `Served by fallback API https://ikabot-api.example.com`. Remove
`CUSTOM_API_ADDRESS` afterwards.

---

## Notes

### The user agent list

`SupportedUserAgents.json` in this directory is mounted over the upstream one.
It is upstream's list plus this fork's fifteen agents from `ikabot/config.py`.

This matters more than it looks. **None of this fork's user agents are in
upstream's list** — its entries are truncated (`Safari/537.3`, missing the
final `6`). So the public server rejects every request ikabot makes with a
`user_agent`, and `apiComm.py` retries with no parameters at all: the token
comes back minted with a *random* agent and the *default* region, while the
login goes out with ikabot's chosen agent. That is precisely the mismatch
upstream PRs #414/#416 set out to remove. Your server accepts the real list,
so the token and the login agree.

Keep the file in step if you ever change `user_agents` in `ikabot/config.py`.

### Updating

```bash
cd /boot/config/plugins/compose.manager/projects/ikabot-api
docker compose build --pull && docker compose up -d
```

Set `API_GIT_REF` in `.env` to a tag or commit sha if you would rather not
track upstream `main` on a box that rebuilds unattended.

### Rotating the key

Change `IKABOT_API_KEY` in the stack's `.env`, `docker compose up -d`, then
update every ikabot `.env`. Old keys stop working immediately, so do the
clients promptly — though a stale key only costs you the fallback, since the
public server is still primary.

### Rate limiting

Caddy has no rate limiter without a custom build. Do it at the edge instead:
Cloudflare → Security → WAF → Rate limiting rules, on the
`ikabot-api.example.com` hostname. A few requests a minute per IP is generous
for two dozen accounts that log in a handful of times a day.

### Logs

```bash
docker logs -f ikabot-api        # token generation, captchas
docker logs -f ikabot-api-gate   # every request, and every 401
docker logs -f ikabot-api-tunnel # tunnel health
```

A run of 401s in the gate log means something is probing with a wrong key —
worth a look, but it is not getting past the gate.
