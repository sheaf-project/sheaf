# Verifying your Sheaf build

This guide explains how to confirm that the Sheaf instance you're using — whether self-hosted or the hosted SaaS — is running the code that's actually published on GitHub. It's aimed at curious users, security-aware self-hosters, and anyone who wants to understand the trust model.

This guide is necessarily technically-oriented but tries to keep assumptions about knowledge to the minimum necessary. It does assume basic familiarity with git (commit hashes, the nature and limitations of git's integrity protections, etc), public-key cryptography, hashing algorithms, and digital signatures. It also assumes basic familiarity with using software on the command line in order to use the automatic verification scripts.

## Why verify?

Sheaf stores GDPR Article 9 special-category data — mental-health context, identity information that could out people, therapy-adjacent notes. Trusting the operator is reasonable for low-stakes apps, but for an app like this, "trust me" isn't enough on its own. So Sheaf gives you tools to verify.

There are limits to what verification can prove. Hardware attestation (a [Trusted Execution Environment](https://en.wikipedia.org/wiki/Trusted_execution_environment), or TEE) is the strongest commercially-viable thing you can do, and is prohibitive in terms of costs and complexity for a small-scale project where the threat model doesn't justify it. What Sheaf *does* offer is two layers of verifiability that cover most realistic concerns:

1. **Image verifiability** — confirm the Docker image being deployed corresponds to a specific public commit, signed by Sheaf's CI.
2. **Frontend verifiability** — confirm the JavaScript your browser is executing right now is byte-for-byte the published frontend code.

The second one is the strong claim, because it's verifiable from your browser without trusting the server.

## What's protected, and what isn't

| Concern | Layer 1 (image) | Layer 3 (frontend) |
|---|---|---|
| "Is this the published code?" | ✅ — verified via cosign | ✅ — verified via SRI + manifest |
| "Did Sheaf's CI build it?" | ✅ — signature ties to the workflow | ✅ — manifest is signed |
| "Could the operator deploy a different image?" | ❌ — operator-attested | ✅ — your browser checks every loaded file |
| "Could someone tamper with assets in transit?" | ✅ — image digest pinned | ✅ — browser refuses bad hashes |
| "Could the host kernel / hypervisor be compromised?" | ❌ | ❌ (would need a TEE) |

The crucial gap Layer 3 closes: a malicious operator can't ship modified frontend code to your browser without you noticing, because the browser checks every file's hash against the published manifest. Layer 1 doesn't have that property — it only proves "an image with this commit exists and was signed."

For Sheaf specifically, this matters because the frontend handles the interesting things: passwords, TOTP codes, the bio-editor textarea, and so on. Pinning the frontend means malicious operator behaviour around those surfaces is detectable.

---

## Layer 1: image verifiability

### What you can check

For any Sheaf instance, you can:

1. `GET /v1/version` to see what commit the backend was built from.
2. Pull that commit's image from GHCR and verify it was signed by Sheaf's official CI workflow.
3. Confirm the image digest matches what was signed (so nothing was swapped after publication).

What you can't check from this alone: that the running backend is *actually* serving the image it claims to be. A malicious operator could lie at the `/v1/version` endpoint. This is a known limitation; Layer 3 is what closes it for browser-loaded code.

### How it works (high level)

When Sheaf releases a new version, GitHub Actions builds the Docker image and uses **[sigstore/cosign](https://github.com/sigstore/cosign)** to sign it. Cosign uses what's called *keyless OIDC signing*:

- There's no private key sitting somewhere that could leak or be otherwise obtained via legal or covert methods.
- Instead, every signature is tied to the GitHub Actions workflow that produced it ("this was signed by `https://github.com/sheaf-project/sheaf/.github/workflows/ci.yml` running on tag `v0.1.1`").
- The signature is recorded in **[Rekor](https://github.com/sigstore/rekor)**, Sigstore's public transparency log — an append-only ledger that anyone can audit.

This means verification doesn't depend on Sheaf's project owner protecting a key — it depends on GitHub's identity provider and Sigstore's public infrastructure, both of which are operated by independent parties with public auditing.

When you verify a signature, you're checking: "This image digest was signed by the Sheaf CI workflow, and the signature is recorded in the public log." If any of those don't hold — wrong workflow, missing log entry, modified image — verification fails.

### How to verify (manual)

Install [cosign](https://docs.sigstore.dev/cosign/system_config/installation/), then:

```sh
# 1. Find out what commit the instance is running.
curl -s https://your-sheaf-instance/v1/version | jq

# 2. Verify the image was signed by Sheaf's CI workflow.
cosign verify ghcr.io/sheaf-project/sheaf:0.1.1 \
  --certificate-identity-regexp "https://github.com/sheaf-project/sheaf/.github/workflows/ci.yml@.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

A successful verification prints the image digest and metadata — the workflow run ID, the git SHA, the tag. A failed verification prints an error explaining why (wrong identity, missing signature, etc.).

You can also inspect the image's OCI labels without running it:

```sh
docker inspect ghcr.io/sheaf-project/sheaf:0.1.1 \
  | jq '.[].Config.Labels."org.opencontainers.image.revision"'
```

This returns the git commit baked into the image at build time, independent of whatever the running backend reports.

### How to verify (automated)

Sheaf ships a verification script at `scripts/verify-release.sh`. Run it against an instance URL:

```sh
./scripts/verify-release.sh https://your-sheaf-instance
```

The script fetches `/v1/version`, runs the cosign verification with the correct workflow identity, compares the image's `org.opencontainers.image.revision` label against the reported commit, and prints a pass/fail summary. Use this for routine spot checks.

---

## Layer 3: frontend verifiability

### What you can check

For any Sheaf instance:

1. Open your browser's devtools. Look at the `<script>` tags on the page — every one has an `integrity="sha384-..."` attribute.
2. Browsers refuse to execute any script whose actual content doesn't match the `integrity` hash. This is enforced by the browser; the server can't lie.
3. Sheaf publishes a `build-manifest.json` listing the expected hash of every file in the frontend bundle, tied to a specific git commit.
4. You can compare what your browser loaded against what the manifest says — and against what *you* would build from the published source.

If everything matches: the JavaScript running in your browser is byte-for-byte identical to what's in the public Sheaf repo at that commit. No server trust required.

### How it works (high level)

**SRI (Subresource Integrity)** is a web-platform feature: when an HTML file says `<script src="/x.js" integrity="sha384-...">`, the browser computes the SHA-384 hash of `x.js` after fetching it and refuses to run the script if the hash doesn't match. It's strong because the check happens on bytes the browser actually received — neither the server nor a man-in-the-middle can fake their way past it.

Sheaf's build process generates these `integrity` attributes automatically for every script and stylesheet, then writes a **build manifest** (a JSON file at `/build-manifest.json`) listing every expected hash. The manifest itself is also signed via cosign, so paranoid users can fetch it from a Sigstore-attested source rather than trusting the server.

The "`index.html` problem": SRI protects everything *except* `index.html`, because `index.html` is what carries the `integrity` attributes. If `index.html` were tampered with, the wrong attributes could be substituted. Mitigation: `index.html` is small enough that you can hash it manually, and its expected hash is in the manifest. So the trust chain is: cosign-attested manifest → `index.html` hash → all referenced files via SRI.

### How to verify (manual, browser-side)

1. Visit your Sheaf instance.
2. Open devtools → Sources or Network → click a script file → look for `integrity` in the request headers / page source.
3. Visit `https://your-sheaf-instance/build-manifest.json`.
4. Compare the hashes. They should match the `integrity=` attributes on your loaded page.

If they match, your browser is loading what Sheaf's CI built.

### How to verify (against the published source)

The strongest claim: rebuild from source and compare hashes.

```sh
git clone https://github.com/sheaf-project/sheaf.git
cd sheaf
git checkout <commit-from-/v1/version>
cd web
npm ci
npm run build

# Now diff your build-manifest against the served one:
diff <(jq -S .files dist/build-manifest.json) \
     <(curl -s https://your-sheaf-instance/build-manifest.json | jq -S .files)
```

If the file lists are identical, the served frontend is the published source.

> **A caveat**: Vite builds aren't perfectly deterministic across machines (some plugins inject timestamps or vary chunk-naming order). In practice most files reproduce exactly because they're content-hashed; if you see a small diff in `index.html` ordering, that's likely build-tooling non-determinism rather than tampering. Moving toward fully reproducible builds is a known goal.

### Cosign-attested manifest verification

For verification independent of the running server, the build manifest is also published as a Sigstore attestation against the `sheaf-web` image:

```sh
cosign verify-attestation --type custom \
  --certificate-identity-regexp "https://github.com/sheaf-project/sheaf/.github/workflows/ci.yml@.*" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/sheaf-project/sheaf-web:0.1.1
```

This pulls the manifest as a Sigstore-attested predicate. Useful for verifying without trusting the instance you're hitting — useful when you want to know what the canonical build is independently of whatever a particular server claims.

---

## Releases and the trust chain

Sheaf publishes signed releases on a manual-approval workflow:

1. A maintainer cuts a tag (`v0.x.y`).
2. CI builds and signs the Docker images automatically.
3. CI **pauses for human approval** before publishing the GitHub release page and uploading verifiable artefacts (frontend tarball, build manifest, SBOM).
4. Approval happens via the GitHub Actions UI by an authorized reviewer.

This means:
- An accidental tag push doesn't create a public release.
- Every release has a sentient reviewer in the loop, named in the GitHub Actions audit log.
- Image signing happens *before* the gate, so even if approval is delayed, the signed image is verifiable from GHCR.

### What you get with a release

- Signed multi-arch Docker images on GHCR.
- A GitHub release page with auto-generated notes from PR titles plus a hand-curated `CHANGELOG.md` summary.
- A frontend tarball (`web-dist-vX.Y.Z.tar.gz`) and `build-manifest.json` as release assets.
- An SPDX SBOM for each image, published as a Sigstore attestation.

---

## What we explicitly do *not* claim

- **Hardware attestation**: Sheaf doesn't run in a TEE. If your threat model includes a malicious host kernel or hypervisor, this design doesn't help you. At that level, you'd want something like AMD SEV-SNP confidential VMs or similar.
- **Backend behaviour matches frontend integrity**: Layer 3 proves the frontend is what you expect; the backend's behaviour beyond that is operator-attested.
- **Reproducible builds**: byte-for-byte reproducibility from source is a future goal, not a guarantee yet. Most of the bundle is reproducible but some non-determinism in HTML/asset ordering is known.
- **Old versions stay verifiable forever**: cosign keyless signatures rely on Rekor (Sigstore's public log). Rekor is an append-only log run by an independent foundation, not Sheaf — so signatures stay verifiable as long as Sigstore stays operational. Realistic horizon: many years, but not "forever."

---

## Questions or concerns?

- File an issue at <https://github.com/sheaf-project/sheaf/issues>.
- Discord: <https://discord.gg/WFaKQPzFx8>.
- If you spot a tampered or mis-signed image, please report it via GitHub security advisory rather than a public issue.
