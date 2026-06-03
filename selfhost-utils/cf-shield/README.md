# cf-shield

Reference break-glass DDoS shield script for a Sheaf deployment on
**AWS + Cloudflare**. Hardens the stack during an active L7 attack by
routing all traffic through Cloudflare with UAM on the webapp host,
revoking direct-origin SG ingress so an attacker who knows the origin
IP can't bypass, and notifying the backend so it can invalidate
opted-out users' sessions.

**This is the version we run in production.** Public Terraform modules
for the full infra shape are a future project; this script is included
as a working reference so selfhosters with similar requirements have
a starting point rather than a blank page.

## What this assumes

- **Hosting on AWS EC2** — uses `aws ec2 revoke-security-group-ingress`.
  Selfhosters on different stacks (Hetzner, OVH, Vultr, bare-metal)
  can adapt by replacing the `revoke_world_ingress` /
  `restore_world_ingress` functions with whatever your firewall layer
  uses (UFW, hosting-provider firewall, hardware appliance, etc.).
- **DNS + edge on Cloudflare** — uses the CF API to flip `proxied` on
  the webapp/API DNS records and set zone-level `security_level`.
- **Two DNS records, distinct webapp + API hosts** — the design
  separates them so a UAM challenge on the webapp doesn't break
  non-browser API clients (mobile apps, webhooks, scripts). If you
  serve both on the same host, you'd need to either accept that
  API clients see UAM challenges during incidents, or restructure
  to expose API on its own subdomain.
- **One Cloudflare Configuration Rule, set up once in the dashboard**,
  scoped to the API host:
    - Phase: HTTP Config Settings (sometimes called "Configuration
      Rules" in the UI)
    - Expression: `(http.host eq "api.example.com")`
    - Action parameter: `security_level = essentially_off`
  Without this rule, the zone-wide `security_level=under_attack` that
  `up` engages will hit the API host and 403 every non-browser
  client. The rule overrides for the API host only.
- **SG rules tagged by description.** The script identifies the
  "world allow" rules to revoke / restore by matching the literal
  string `HTTPS from world` in the rule's Description field. Your
  SG should have something like:
    - IPv4 ingress, port 443, source `0.0.0.0/0`,
      Description: `HTTPS from world (cf-shield revokes this during incidents)`
    - IPv6 ingress, port 443, source `::/0`,
      Description: `HTTPS from world v6 (cf-shield revokes this during incidents)`
  Plus a permanent SG rule allowing port 443 from the Cloudflare
  IPv4 + IPv6 CIDR ranges (these stay open and are how CF reaches
  the origin once world ingress is revoked). See the [Cloudflare IP
  ranges page](https://www.cloudflare.com/ips/).
- **Backend is Sheaf** with `SHIELD_MODE_ENABLED=true` and
  `SHIELD_MODE_WEBHOOK_SECRET=<random>` set in the backend's env. The
  same secret value is passed to this script as
  `SHEAF_SHIELD_WEBHOOK_SECRET`. The HMAC contract is documented in
  `docs/METRICS.md` (look for `sheaf_cf_shield_*` metrics) and in the
  backend code at `sheaf/api/v1/shield_mode.py`.

## What this does, step by step

`cf-shield up`:

1. **CF: flip both DNS records to `proxied=true`.** Webapp + API both
   route through Cloudflare.
2. **CF: set zone `security_level=under_attack`.** Webapp gets the UAM
   JS interstitial; the Configuration Rule above keeps API at
   `essentially_off` so non-browser clients keep working.
3. **Backend: POST `{"active": true}` with HMAC signature** to
   `/v1/internal/shield-mode/state`. Backend uses this to invalidate
   sessions for users who've opted into the "disable CDN during DDoS"
   preference.
4. **SG: revoke the world-allow ingress rules** so anyone who knows
   the origin IP can't bypass Cloudflare. CF IPs stay allowed.

`cf-shield down` reverses, in safe order:

1. SG: restore world ingress (so the webhook can reach origin).
2. Backend: POST `{"active": false}`.
3. CF: flip both DNS records back to `proxied=false`.
4. CF: set zone `security_level=medium`.

`cf-shield status` reads the current CF state, SG rules, and backend
shield-mode endpoint (if `SHEAF_SHIELD_WEBHOOK_URL` is set).

## Usage

```sh
# Required env
export CF_API_TOKEN=<paste from your secret store>
export CF_ZONE_ID=<your CF zone id>
export CF_APP_RECORD_ID=<webapp DNS record id>
export CF_API_RECORD_ID=<api DNS record id>
export AWS_PROFILE=<your AWS profile>
export SG_ID=<your instance SG id>

# Optional (skips the backend notify if unset)
export SHEAF_SHIELD_WEBHOOK_URL=https://api.example.com/v1/internal/shield-mode/state
export SHEAF_SHIELD_WEBHOOK_SECRET=<same value as backend SHIELD_MODE_WEBHOOK_SECRET>

./cf-shield status        # check current state, no changes
./cf-shield up            # shield ON
./cf-shield down          # shield OFF, restore steady state
```

For runtime safety, source these from a shell helper rather than
typing tokens into your terminal history. Example:

```sh
cat > ~/.cf-shield.env <<'EOF'
# Source me, don't commit.
export AWS_PROFILE=prod
export CF_API_TOKEN=$(your secret-fetch command)
export CF_ZONE_ID=...
export CF_APP_RECORD_ID=...
export CF_API_RECORD_ID=...
export SG_ID=...
export SHEAF_SHIELD_WEBHOOK_URL=https://api.example.com/v1/internal/shield-mode/state
export SHEAF_SHIELD_WEBHOOK_SECRET=$(your secret-fetch command)
EOF
chmod 600 ~/.cf-shield.env

. ~/.cf-shield.env && ./cf-shield status
```

## Cloudflare API token scopes

When creating the token in the Cloudflare dashboard:

| Permission | Scope |
|---|---|
| Zone → DNS → Edit | Specific zone: your zone |
| Zone → Zone Settings → Edit | Specific zone: your zone |

(Optionally lock down by Client IP filtering to your incident-response
workstation.)

## Drift

Your IaC tool (Terraform, Pulumi, OpenTofu, ...) will notice the
following while shield is active:

- **DNS record `proxied=true`** when your code says `false`.
- **Missing world-allow SG rules** when your code says they exist.

`cf-shield down` reverses both, and the next IaC apply reconciles. If
the drift noise during an incident bothers you, add a
`lifecycle.ignore_changes = [proxied]` on the relevant DNS record
resource (Terraform syntax; equivalents exist in other tools).

## Adapting

The script is ~200 lines of bash. The pieces you'd change for a
different hosting stack:

- **`revoke_world_ingress` / `restore_world_ingress`** — replace AWS
  CLI calls with whatever your firewall layer needs. Could be
  `ufw delete allow 443/tcp` / `ufw allow 443/tcp` on a single box,
  or hitting your hosting provider's firewall API.
- **`AWS_PROFILE` / `SG_ID` env vars** — drop or replace if you're
  not on AWS.

The Cloudflare and backend-webhook pieces are reusable as long as you
use Cloudflare as your edge and Sheaf as your backend.

## License

See the repository LICENSE.
