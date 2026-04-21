# cf-image-worker

Cloudflare Worker that fronts a private S3 bucket with HMAC-validated,
expiring URLs for Sheaf image serving. Used for the **S3 + signed URLs +
CDN** paradigm described in `docs/SELFHOSTING.md`.

## What it does

1. Receives requests at your image hostname (e.g. `images.example.com/{key}?token=…&expires=…`).
2. Validates the HMAC token against `FILE_SIGNING_KEY` and checks `expires`.
3. If valid, fetches the object from S3 using AWS SigV4 credentials.
4. Returns it with cache headers matched to the token's remaining lifetime.
5. Cloudflare's edge cache serves subsequent hits within the signing window without re-invoking the Worker.

Invalid tokens, expired tokens, malformed keys, and keys outside the allow-list return 403 at the edge.

## When to use this

Pick this setup when you need all three of:

- **Private bucket** — nothing in S3 is publicly readable.
- **Expiring URLs** — leaked URLs stop working within minutes to hours.
- **CDN caching** — image traffic never hits your app server.

If you don't need one of those, pick a simpler paradigm from the self-hosting docs.

## Deployment

```sh
cd selfhost-utils/cf-image-worker
npm install
```

Edit `wrangler.toml`:
- `S3_BUCKET`, `S3_REGION`, optionally `S3_ENDPOINT`.
- Uncomment and set `routes` to your image hostname.
- Adjust `ALLOWED_KEY_PREFIXES` if you've configured Sheaf with non-default storage layout.

Set secrets (each runs an interactive prompt):

```sh
npx wrangler secret put FILE_SIGNING_KEY
npx wrangler secret put AWS_ACCESS_KEY_ID
npx wrangler secret put AWS_SECRET_ACCESS_KEY
```

`FILE_SIGNING_KEY` must match exactly what you set on the Sheaf backend — the backend HMACs the URL with this key, the Worker verifies with the same key. Generate one with:

```sh
openssl rand -hex 32
```

The IAM user for `AWS_ACCESS_KEY_ID` needs only `s3:GetObject` on `arn:aws:s3:::your-bucket/*`. Do **not** give it write or list permissions.

Deploy:

```sh
npx wrangler deploy
```

On the Sheaf side, set:

```env
IMAGE_SERVING=signed
S3_PUBLIC_URL=https://images.example.com
FILE_SIGNING_KEY=<same value as the Worker secret>
```

## Cost expectations

- **Workers:** free tier is 100k requests/day (~3M/mo). The Worker only runs on cache misses; with a 1-hour signing window and a normal hit rate, most image loads skip it entirely. Small/mid instances stay free-tier indefinitely. Paid plan ($5/mo) covers 10M requests.
- **S3 egress:** every cache miss pulls the full object from S3 to Cloudflare at $0.09/GB. For avatars (<500KB), this is noise unless you're purging the CF cache frequently.
- **CF bandwidth to end users:** free on Cloudflare's standard plan.

If egress becomes material, migrating the bucket from S3 to R2 eliminates it — R2→Worker is free, and Workers get a native R2 binding that makes the SigV4 code in this Worker unnecessary. That's a future port, not required to operate today.

## Security notes

- The Worker serves **only** on GET/HEAD. POST/PUT/DELETE are rejected.
- `ALLOWED_KEY_PREFIXES` is enforced before any S3 call, so a leaked valid token can't be used to reach unrelated objects in the bucket (e.g. if the same bucket holds non-user data).
- Path traversal (`..`, leading `/`, null bytes) is rejected.
- `x-amz-*` and `server` headers from S3 are stripped before returning to the client — no bucket-identifying leakage.
- HMAC verification uses `crypto.subtle.verify`, which is constant-time.
- `FILE_SIGNING_KEY` is stored as a Wrangler secret, not in `wrangler.toml`. Don't commit it.
- Upstream fetch uses SigV4, so the bucket can stay fully private — no bucket policy changes required.

## Testing locally

`npx wrangler dev` starts a local dev server on `localhost:8787`. Generate a signed URL from your Sheaf backend (upload an image and inspect the response), swap the hostname for `localhost:8787`, and hit it with curl:

```sh
curl -v "http://localhost:8787/avatars/{id}/{uuid}.png?token=...&expires=..."
```

You should get the image bytes. Tamper with the token → 403. Let the token expire → 403.
