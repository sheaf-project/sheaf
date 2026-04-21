/**
 * Sheaf image signing Worker.
 *
 * Sits in front of a private S3 bucket on a Cloudflare-proxied hostname.
 * Validates HMAC-signed URLs issued by the Sheaf backend, fetches the
 * object from S3 via SigV4, caches at the edge keyed by the full URL.
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

async function signS3Get({ bucket, region, endpoint, key, accessKey, secretKey }) {
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
    "GET",
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

export default {
  async fetch(request, env) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return reject(405, "Method not allowed");
    }

    const url = new URL(request.url);
    const key = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    const token = url.searchParams.get("token");
    const expires = url.searchParams.get("expires");

    if (!key || !token || !expires) return reject(403);

    if (!isAllowedKey(key, env.ALLOWED_KEY_PREFIXES)) return reject(403);

    const expiresInt = Number.parseInt(expires, 10);
    if (!Number.isFinite(expiresInt)) return reject(403);
    const nowSec = Math.floor(Date.now() / 1000);
    if (expiresInt <= nowSec) return reject(403);

    const signingKey = required(env, "FILE_SIGNING_KEY");
    const valid = await verifyToken(signingKey, key, expires, token);
    if (!valid) return reject(403);

    // Cache lookup — full URL is the key, so each signing window has its
    // own entry, and expired/invalid tokens never reach cache.
    const cache = caches.default;
    const cacheKey = new Request(url.toString(), { method: "GET" });
    const cached = await cache.match(cacheKey);
    if (cached) return cached;

    const signed = await signS3Get({
      bucket: required(env, "S3_BUCKET"),
      region: env.S3_REGION || "us-east-1",
      endpoint: env.S3_ENDPOINT || "",
      key,
      accessKey: required(env, "AWS_ACCESS_KEY_ID"),
      secretKey: required(env, "AWS_SECRET_ACCESS_KEY"),
    });

    const origin = await fetch(signed.url, { headers: signed.headers });
    if (!origin.ok) {
      return reject(origin.status === 404 ? 404 : 502, "Origin error");
    }

    const headers = new Headers(origin.headers);
    headers.delete("x-amz-request-id");
    headers.delete("x-amz-id-2");
    headers.delete("server");
    headers.set("x-content-type-options", "nosniff");
    // Cache until the token expires — never longer.
    const ttl = Math.max(1, expiresInt - nowSec);
    headers.set("cache-control", `public, max-age=${ttl}, immutable`);

    const body = await origin.arrayBuffer();
    const response = new Response(body, { status: 200, headers });
    // ctx.waitUntil would be cleaner but we don't have ctx in this shape;
    // awaiting is fine for a hot path that's already doing origin IO.
    await cache.put(cacheKey, response.clone());
    return response;
  },
};
