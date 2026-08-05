/**
 * Sheaf image signing Worker.
 *
 * Sits in front of a private S3 bucket on a Cloudflare-proxied hostname.
 * Validates HMAC-signed URLs issued by the Sheaf backend, fetches the
 * object from S3 via SigV4, caches at the edge under a canonical URL.
 *
 * See selfhost-utils/cf-image-worker/README.md for deployment.
 */

const encoder = new TextEncoder();
const EMPTY_SHA256 =
  "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

// ----- helpers -----------------------------------------------------------

function bytesToHex(bytes) {
  let out = "";
  for (const b of bytes) out += b.toString(16).padStart(2, "0");
  return out;
}

function hexToBytes(hex) {
  if (hex.length % 2 !== 0) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    const byte = parseInt(hex.substr(i * 2, 2), 16);
    if (Number.isNaN(byte)) return null;
    out[i] = byte;
  }
  return out;
}

async function hmacSha256(keyBytes, data) {
  const key = await crypto.subtle.importKey(
    "raw",
    keyBytes,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const msg = typeof data === "string" ? encoder.encode(data) : data;
  const sig = await crypto.subtle.sign("HMAC", key, msg);
  return new Uint8Array(sig);
}

async function sha256Hex(data) {
  const msg = typeof data === "string" ? encoder.encode(data) : data;
  const hash = await crypto.subtle.digest("SHA-256", msg);
  return bytesToHex(new Uint8Array(hash));
}

// ----- token validation --------------------------------------------------

async function verifyToken(signingKeyRaw, key, expires, token) {
  const expected = hexToBytes(token);
  if (!expected || expected.length !== 32) return false;
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    encoder.encode(signingKeyRaw),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const msg = encoder.encode(`${key}:${expires}`);
  return crypto.subtle.verify("HMAC", cryptoKey, expected, msg);
}

// ----- path validation ---------------------------------------------------

function isAllowedKey(key, allowedPrefixes) {
  if (!key) return false;
  if (key.includes("..") || key.startsWith("/") || key.includes("\0")) {
    return false;
  }
  if (!allowedPrefixes) return true;
  const prefixes = allowedPrefixes
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);
  if (prefixes.length === 0) return true;
  return prefixes.some((p) => key.startsWith(p));
}

// ----- SigV4 GET signer --------------------------------------------------

async function signS3Request({
  bucket,
  region,
  endpoint,
  key,
  accessKey,
  secretKey,
  method,
}) {
  let host;
  let pathname;
  if (endpoint) {
    const base = new URL(endpoint);
    host = base.host;
    const basePath = base.pathname.replace(/\/$/, "");
    pathname = `${basePath}/${bucket}/${key.split("/").map(encodeURIComponent).join("/")}`;
  } else {
    host = `${bucket}.s3.${region}.amazonaws.com`;
    pathname = `/${key.split("/").map(encodeURIComponent).join("/")}`;
  }

  const now = new Date();
  const amzDate = now
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\.\d{3}/, "");
  const dateStamp = amzDate.slice(0, 8);

  const canonicalHeaders =
    `host:${host}\n` +
    `x-amz-content-sha256:${EMPTY_SHA256}\n` +
    `x-amz-date:${amzDate}\n`;
  const signedHeaders = "host;x-amz-content-sha256;x-amz-date";
  const canonicalRequest = [
    method,
    pathname,
    "", // empty query string
    canonicalHeaders,
    signedHeaders,
    EMPTY_SHA256,
  ].join("\n");

  const scope = `${dateStamp}/${region}/s3/aws4_request`;
  const stringToSign = [
    "AWS4-HMAC-SHA256",
    amzDate,
    scope,
    await sha256Hex(canonicalRequest),
  ].join("\n");

  const kDate = await hmacSha256(encoder.encode(`AWS4${secretKey}`), dateStamp);
  const kRegion = await hmacSha256(kDate, region);
  const kService = await hmacSha256(kRegion, "s3");
  const kSigning = await hmacSha256(kService, "aws4_request");
  const signature = bytesToHex(await hmacSha256(kSigning, stringToSign));

  const authorization =
    `AWS4-HMAC-SHA256 Credential=${accessKey}/${scope}, ` +
    `SignedHeaders=${signedHeaders}, Signature=${signature}`;

  return {
    url: `https://${host}${pathname}`,
    headers: {
      Authorization: authorization,
      "x-amz-content-sha256": EMPTY_SHA256,
      "x-amz-date": amzDate,
    },
  };
}

// ----- entrypoint --------------------------------------------------------

function reject(status, body = "Forbidden") {
  return new Response(body, {
    status,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function required(env, name) {
  if (!env[name]) throw new Error(`Missing binding/secret: ${name}`);
  return env[name];
}

function signedQuery(url) {
  // There is one canonical spelling for a capability URL. Besides avoiding
  // ambiguous validation, this prevents a caller with a valid URL from
  // manufacturing cache misses by adding, duplicating, or respelling query
  // parameters.
  const entries = [...url.searchParams.entries()];
  if (entries.length !== 2) return null;
  if (
    url.searchParams.getAll("token").length !== 1 ||
    url.searchParams.getAll("expires").length !== 1
  ) {
    return null;
  }
  if (entries.some(([name]) => name !== "token" && name !== "expires")) {
    return null;
  }

  const token = url.searchParams.get("token");
  const expires = url.searchParams.get("expires");
  if (!token || !/^[0-9a-f]{64}$/.test(token)) return null;
  if (!expires || !/^(0|[1-9][0-9]*)$/.test(expires)) return null;
  return { token, expires };
}

function canonicalCacheKey(url, key, token, expires) {
  const canonical = new URL(url.origin);
  canonical.pathname = `/${key.split("/").map(encodeURIComponent).join("/")}`;
  canonical.searchParams.set("token", token);
  canonical.searchParams.set("expires", expires);
  return new Request(canonical.toString(), { method: "GET" });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return reject(405, "Method not allowed");
    }

    const url = new URL(request.url);
    let key;
    try {
      key = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    } catch {
      return reject(403);
    }
    const query = signedQuery(url);

    if (!key || !query) return reject(403);
    const { token, expires } = query;

    if (!isAllowedKey(key, env.ALLOWED_KEY_PREFIXES)) return reject(403);

    const expiresInt = Number(expires);
    if (!Number.isSafeInteger(expiresInt)) return reject(403);
    const nowSec = Math.floor(Date.now() / 1000);
    if (expiresInt <= nowSec) return reject(403);

    const signingKey = required(env, "FILE_SIGNING_KEY");
    const valid = await verifyToken(signingKey, key, expires, token);
    if (!valid) return reject(403);

    // Both accepted query orders and equivalent path encodings converge on
    // this key. Expired and invalid capabilities never reach the cache.
    const cache = caches.default;
    const cacheKey = canonicalCacheKey(url, key, token, expires);
    const cached = await cache.match(cacheKey);
    if (cached) {
      const headers = new Headers(cached.headers);
      const ttl = Math.max(1, expiresInt - nowSec);
      headers.set("cache-control", `public, max-age=${ttl}, immutable`);
      return new Response(request.method === "HEAD" ? null : cached.body, {
        status: cached.status,
        headers,
      });
    }

    const signed = await signS3Request({
      bucket: required(env, "S3_BUCKET"),
      region: env.S3_REGION || "us-east-1",
      endpoint: env.S3_ENDPOINT || "",
      key,
      accessKey: required(env, "AWS_ACCESS_KEY_ID"),
      secretKey: required(env, "AWS_SECRET_ACCESS_KEY"),
      method: request.method,
    });

    const origin = await fetch(signed.url, {
      method: request.method,
      headers: signed.headers,
    });
    if (!origin.ok) {
      return reject(origin.status === 404 ? 404 : 502, "Origin error");
    }

    const headers = new Headers(origin.headers);
    for (const name of [...headers.keys()]) {
      if (name.startsWith("x-amz-")) headers.delete(name);
    }
    headers.delete("server");
    headers.set("x-content-type-options", "nosniff");
    // Cache until the token expires — never longer.
    const ttl = Math.max(1, expiresInt - nowSec);
    headers.set("cache-control", `public, max-age=${ttl}, immutable`);

    const response = new Response(request.method === "HEAD" ? null : origin.body, {
      status: origin.status,
      headers,
    });
    // A HEAD miss is deliberately cheap and does not populate a bodyless
    // entry that could shadow a later GET.
    if (request.method === "GET") {
      const put = cache.put(cacheKey, response.clone());
      if (ctx?.waitUntil) ctx.waitUntil(put);
      else await put;
    }
    return response;
  },
};
