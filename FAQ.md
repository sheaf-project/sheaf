# FAQ

## General

### What is Sheaf?

Sheaf is an open-source plural system tracker. It lets you track members, switches (fronting), groups, custom fields, and more. It's a self-hostable replacement for SimplyPlural.

### Why does Sheaf exist?

[Simply Plural is shutting down](https://apparyllis.com/simply-plural-will-be-discontinued/). The alternatives that appeared are either closed-source, local-only, or don't have credible infrastructure behind them. Sheaf is built by people who make a living running large-scale systems and shipping production-quality code, with a focus on doing it properly and not sacrificing feature-compatibility for ease-of-implementation.

### Is Sheaf free?

Yes. Sheaf is free and open-source software under the AGPL-3.0 license. You can self-host it at no cost. There may be a hosted option in the future with a free tier and a paid tier — the paid tier would fund development and infrastructure, not gate core features behind a paywall.

### Can I import my SimplyPlural data?

Yes. Export your data from SimplyPlural, then use the import feature in Sheaf. You can choose exactly what to import — specific members, front history, custom fields, groups.

### Can I import from PluralKit?

Yes, both ways: upload a `pk;export` JSON file, or paste the token from `pk;token` to pull live from the PluralKit API. PK's switch log is converted into Sheaf's front-interval model, and each member's PK HID is stored on the imported member so you can keep cross-referencing between the two. Tokens are forwarded once and never stored. See [docs/IMPORT.md](docs/IMPORT.md) for details.

### Does Sheaf have mobile apps?

Yes, iOS and Android apps are in development.

## Self-Hosting

### I want to run my own hosted version of Sheaf. Can I?

Yes. The AGPL license allows this. If you run a modified version as a public service, you must publish your modifications under the same license. Be aware of the legal implications of hosting other people's sensitive identity data.

### What do I need to self-host?

Docker and Docker Compose, and a machine with >=512MB of RAM. That's it. `cp .env.example .env && docker compose up -d` gets you running, all you need to wire yourself is an ingress.

### Do I need to be technical to self-host?

You need to be comfortable with a terminal and editing a config file. If you've ever set up a Minecraft server or a Discord bot, you can probably handle this. In addition, you will need a domain, the ability to obtain a SSL/TLS certificate (free with LetsEncrypt), and to configure a front-end proxy for TLS termination.

### Can I host my own Sheaf instance and provide access to members of the public? Can I charge for access?

Yes. However, you should consider your own risk factors and threat model for the reasons outlined above. The bigger you get, the higher the chance is you will eventually receive a subpoena for user data - make sure you have mechanisms in place to handle this appropriately, and seek professional legal advice if necessary.

Charging or otherwise receiving some form of personal gain in return for access is allowed.

### Do I have to release modifications to the source for my selfhosted version if I am not offering it to the public?

No. The AGPL only applies when run for the public. There are no such restrictions on personal instances, or those run for a friend group, organisation, or otherwise not open to the public. Note that merely *accepting payment* for access, while permitted, does not constitute nonpublic if anyone is able to pay for and use the service with no further requirements, and the full AGPL terms apply.

### Is my data encrypted?

Application-level encryption protects email addresses and TOTP secrets at rest. Beyond that, you should encrypt your server's disk (LUKS, encrypted EBS volumes, etc). See the Security section of the README for details.

### Can I use a cloud database instead of the Docker one?

Yes. Any PostgreSQL 16+ instance works — just set `DATABASE_URL` in your `.env`. AWS RDS, Google Cloud SQL, Supabase, Neon, etc. Same for Redis.

### Is it safe to use someone's selfhosted server?

It depends on the owner more than anything else - Sheaf's codebase is designed to be resilient and secure-by-default, but the nature of open source software means anyone may modify it for personal use. Your trust in a server should only go as far as your trust in the admin, for all system tracking apps, not just this one, in addition to anything else that is not running on your own computer.

The biggest realworld risk is probably of a hosted instance shutting down due to the owner losing interest or being unable to cover operational costs. Remember you can always export your data and import it on a new server, assuming no incompatible modifications to the server code (and even if those exist, data exports are simply structured enough that editing them by hand to fix compatibility issues is certainly possible, although not something the project maintainers can offer support with).

## Privacy & Security

### What data does Sheaf collect?

On a self-hosted instance: only what you put in. There is no telemetry, no analytics, no phoning home.

A future hosted service would collect only what's needed to provide the service (account email, your system data). No selling data, no ads, ever.

### Is Sheaf end-to-end encrypted?

No. The server encrypts sensitive fields (email, TOTP secrets) at rest, but it holds the encryption key when running. This means a server operator *could* read data if they wanted to (they shouldn't, but technically could).

True end-to-end encryption (where the server can't read your data at all) is a fundamentally different architecture that also makes features like server-side search impossible. If you want that level of protection, self-host, and secure the database with disk encryption at the server level where you control the encryption key yourself.

### Why isn't the hosted version end-to-end encrypted?

Other than the aforementioned lack of *support* for E2EE, because the people running it are not willing to go to prison rather than comply with legal requests, and the only other option would be to shut down, as happened to the original incarnation of [Lavabit](https://en.wikipedia.org/wiki/Lavabit) and many other similar services. E2EE means the operator genuinely cannot comply with legal requests for data, which creates serious legal risk far beyond what this project's maintainers are willing to accept.

### What about if you hosted it in Switzerland/South America/$random_island_nation/outer space/etc.?

No. Jurisdiction is not a defence against legal requests through official channels. In particular, many services that specifically use Switzerland as a selling point 'conveniently' neglect to mention that the Swiss government does in fact respond to and assist with legal requests from other countries, and only refuses to do so when they directly target a Swiss citizen. 99.999% of "we host in foo jurisdiction" as marketing copy is nothing but pure security theater.

### Is my data GDPR-compliant?

Sheaf treats all system data as GDPR Article 9 special category data (data concerning health, or data revealing information about identity). Self-hosted instances are your own responsibility. A future hosted service would be operated with full GDPR compliance. We believe privacy is a fundamental human right, and in all honesty, aren't interested in your data past the duty of an admin to keep their users safe, and its usefulness to *you*.

### What about PluralKit integration?

One-shot import is supported today (file upload or live API via `pk;token`). Bidirectional sync (pushing switches to PK and/or continuously pulling from PK with conflict resolution) is on the roadmap as a follow-up; it needs careful design work around foreign-ID tracking and merge semantics.

## Contributing

### Can I contribute?

Yes! See [CONTRIBUTING.md](CONTRIBUTING.md). We welcome code contributions, bug reports, feature suggestions, and documentation improvements.

### What contributions are you most interested in receiving?

At this time, probably the web UI and Android app, although PRs are welcome for all aspects.

## Terminology

### What does "system" mean?

In the context of plurality, a system is the collective term for all the people (members, headmates, alters — terminology varies) who share a body. Sheaf uses "system" as the top-level organizational unit.

### What does "fronting" mean?

Fronting is when a member is actively in control of or present in the body. Sheaf tracks front history so you can log who's fronting when.

### What's a "sysmed"?

Short for "sysmedicalist" (a loanword from "transmedicalist" given the extreme parallels between the two groups) — someone who believes plurality is exclusively a medical condition (specifically DID/OSDD), gatekeeps who "should" count as plural, and believes that people who don't have a clinical diagnosis of DID or OSDD aren't "really" plural, often extending to outright denying the validity of non-disordered and/or endogenic systems. This gatekeeping is not welcome in Sheaf's community. See our [Code of Conduct](CODE_OF_CONDUCT.md).
