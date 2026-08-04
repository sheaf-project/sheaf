import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import worker from "../src/worker.js";

const signingKey = "test-signing-key";
const key = "avatars/user/image.png";

const env = {
  FILE_SIGNING_KEY: signingKey,
  S3_BUCKET: "bucket",
  S3_REGION: "us-east-1",
  AWS_ACCESS_KEY_ID: "access",
  AWS_SECRET_ACCESS_KEY: "secret",
  ALLOWED_KEY_PREFIXES: "avatars/,bios/,banners/",
};

const originalFetch = globalThis.fetch;
const originalCaches = globalThis.caches;

afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.caches = originalCaches;
});

async function tokenFor(expires) {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(signingKey),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    cryptoKey,
    new TextEncoder().encode(`${key}:${expires}`),
  );
  return [...new Uint8Array(signature)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function futureExpiry() {
  return String(Math.floor(Date.now() / 1000) + 3600);
}

function installCache() {
  const entries = new Map();
  globalThis.caches = {
    default: {
      async match(request) {
        return entries.get(request.url)?.clone();
      },
      async put(request, response) {
        entries.set(request.url, response.clone());
      },
    },
  };
  return entries;
}

function context() {
  const pending = [];
  return {
    pending,
    waitUntil(promise) {
      pending.push(promise);
    },
  };
}

test("rejects extra and duplicate signed query parameters", async () => {
  installCache();
  let originCalls = 0;
  globalThis.fetch = async () => {
    originCalls += 1;
    return new Response("origin");
  };
  const expires = futureExpiry();
  const token = await tokenFor(expires);

  for (const query of [
    `token=${token}&expires=${expires}&bust=1`,
    `token=${token}&token=${token}&expires=${expires}`,
    `token=${token}&expires=${expires}&expires=${expires}`,
  ]) {
    const response = await worker.fetch(
      new Request(`https://images.example/${key}?${query}`),
      env,
      context(),
    );
    assert.equal(response.status, 403);
  }
  assert.equal(originCalls, 0);
});

test("canonical cache key merges accepted query order", async () => {
  installCache();
  let originCalls = 0;
  globalThis.fetch = async () => {
    originCalls += 1;
    return new Response("image", {
      headers: { "content-type": "image/png", "x-amz-version-id": "private" },
    });
  };
  const expires = futureExpiry();
  const token = await tokenFor(expires);
  const ctx = context();

  const first = await worker.fetch(
    new Request(
      `https://images.example/${key}?expires=${expires}&token=${token}`,
    ),
    env,
    ctx,
  );
  assert.equal(await first.text(), "image");
  assert.equal(first.headers.get("x-amz-version-id"), null);
  await Promise.all(ctx.pending);

  const second = await worker.fetch(
    new Request(
      `https://images.example/${key}?token=${token}&expires=${expires}`,
    ),
    env,
    context(),
  );
  assert.equal(await second.text(), "image");
  assert.equal(originCalls, 1);
});

test("HEAD miss stays bodyless and uses an upstream HEAD", async () => {
  const entries = installCache();
  let upstreamMethod;
  globalThis.fetch = async (_url, init) => {
    upstreamMethod = init.method;
    return new Response("body that must not be returned", {
      headers: { "content-type": "image/png" },
    });
  };
  const expires = futureExpiry();
  const token = await tokenFor(expires);

  const response = await worker.fetch(
    new Request(
      `https://images.example/${key}?token=${token}&expires=${expires}`,
      { method: "HEAD" },
    ),
    env,
    context(),
  );

  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
  assert.equal(upstreamMethod, "HEAD");
  assert.equal(entries.size, 0);
});
