# AgentBoom as a service — architecture

How the open-source framework becomes a hosted product on the owl cluster:
non-developers launch and manage a private, self-evolving agent from
agentboom.dev; the agent (not the user) owns engineering quality, security,
scaling and self-repair. Source of truth for the framework stays
`docs/plan.md`; this doc covers the *service* layer. (2026-08-28.)

## 1. Product shape & naming

Three surfaces, three names, one brand:

| Surface | Name | Repo | Open? |
|---|---|---|---|
| OSS framework: CLI `agentboom`, Python+TS SDKs, templates, registry | **agentboom-sdk** (rename of `agentboom`) | `agent-boom/agentboom-sdk` | yes |
| The per-user product: one self-evolving agent + its platform ("the OS for your life") | **agentboom** (the agent's default name is "boom") | `agent-boom/agentboom-agent` (reference instance) | no |
| The SaaS: marketing front door + private console to launch/manage agents | **agentboom.dev** | `agent-boom/agentboom-website` | yes |

Rationale: "agentboom" is the brand/URL; the OSS artifact is an SDK; the
per-user product is an "OS" (matches the north star). The agent's **default
name is "boom"** (marketing, 2026-08-28) — every user can rename theirs at
any time; "boom" is only the out-of-the-box identity.

## 2. Repos & org

- Move agentboom / agentboom-agent / agentboom-website from `NeuralChowder`
  to the **`agent-boom` org** (user-directed).
- **No new monorepo needed for the framework.** The xema pattern is:
  `xema-monorepo/submodules/*` for the *shared* services only. agentboom
  should NOT vendor those — it *consumes* them. A thin `agent-boom/agentboom-deploy`
  repo (like `xema-deploy`) holds the Helm charts + per-deployment values +
  the `deploy-service` dispatch workflow. Public agent repos dispatch to it.
- One **org-level self-hosted runner** on owl (label `owl-deploy`), shared
  across agent-boom repos, single Dockerfile (mirror
  `/home/edup/actions-runner-adjudica`).

## 3. Shared services: reuse, don't rebuild

Already on owl in `shared-services` (NestJS+Prisma+Postgres, multi-tenant by
Keycloak `realmId`):

- **identity-api** — Keycloak control plane (orgs/roles/memberships). We add
  an `agentboom` realm (provision script modeled on adjudica's
  `provision-adjudica-realm.py`) and register agentboom as a client.
- **billing-api** — Stripe plans/subscriptions/credits/metered usage +
  `/v1/verify` entitlements; service auth via static `st_<64hex>` tokens.
- **llm-gateway-api** — OpenAI-compatible proxy + Redis credit pre-flight +
  usage metering. This is the backbone for model tiers + token visibility.
- **event-hub-api** — CloudEvents bus for cross-service reactions.
- **outbound-email-api** — transactional mail.
- **secrets-api** — envelope-encrypted (AES-256-GCM) store. **NOT yet on owl**;
  it is the right home for per-user agent credentials and a prerequisite of
  newer identity-api images. Deploying it is a deliberate, confirmed step.

We build: the **agentboom control plane** (launch/suspend/recover per-user
agents, disk-quota enforcement, console API) and the **per-user agent
runtime** (the existing compose stack, containerised per user).

## 4. Per-user isolation, data/code split, recovery

Hard requirements: agents must never read each other's data; user data must
never leak; credentials must not live on the agent's data volume.

- One **namespace per tenant tier or per user** (decide; per-user is cleanest
  for isolation) + a **NetworkPolicy** default-deny; agents reach only the
  shared-services FQDNs they're entitled to, never each other.
- **Per-user PVC** for the agent's *data* (memories, DB, files). Separate
  **PVC/volume for code** so code recovery never touches data.
- **Credentials in secrets-api** (and/or SealedSecrets for infra), injected
  at runtime; never baked into the image or the data volume. The agent's
  vault mini-app becomes a thin client over secrets-api.
- **Per-agent internal git (no remote):** the agent's *code* dirs
  (`platform/`, `.qwen-docker/` minus data) are a local git repo; the
  self-evolve loop commits after each verified change. User *data* is
  gitignored. Recovery = check out the last good commit; data is untouched.
  This is the "recover without losing data" guarantee.
- Persistence must also work **locally for dev**: the same compose stack runs
  on a laptop with local volumes; secrets-api/identity are optional locally
  (fall back to the local vault) so dev never needs the cluster.

## 5. Billing & model tiers (economic → premium)

Mirror adjudica/concursos: continuous tiers the user upgrades/downsizes.

- **billing-api** holds plans = price + included credits + **disk quota**.
- **llm-gateway-api** meters tokens; model *tiers* (economic/mid/premium) are
  provider routes with different credit costs. The agent picks a tier **per
  task category** (policy in the agent; user sets a budget). The console
  shows "where tokens are being spent" per category/tier.
- **Disk quota** is enforced by the control plane; on **downgrade** the user
  cannot shrink below current usage — the agent helps them prune (surfaces
  largest/oldest data, proposes deletions) until they fit, then the tier
  changes. No mid-cycle data loss; workspace stays active through the cycle.
- Pricing ladder (user-specified): $7 / $19 / $29 / $50 / $75 / $120 / $150 /
  $250+, each raising credits + disk. Stripe via the existing `~/vault/stripe`
  token, **test mode first**.

## 6. Website reorientation (agentboom.dev)

Follow consumer conventions (researched):
- Hero = ownership + outcome ("Your own self-evolving agent. Your data, your
  machine, your rules."), time promise ("running in minutes"), single CTA.
- Nav in product language (Product / Pricing / Security / How it works);
  **one** clearly-labeled "Developers" item → SDK/GitHub/docs.
- Trust as four plain guarantees: isolation ("your agent never sees anyone
  else's data"), "never used to train AI", encryption at rest, editable
  memory + every action logged.
- Free-self-hosted → paid-managed ladder; pause-on-exhaustion ("no surprise
  bills"); downgrade retains data.
- Private **console** (Keycloak login) to launch/manage agents, distinct from
  each agent's own dashboard (which may still expose public apps).

## 7. Deploy / CI

- Mirror the house pattern: cloudflared tunnel (agentboom.dev wildcard
  already exists → NodePort 30500) + Helm digest-tag deploys on the
  `owl-deploy` runner; product workloads in an `agentboom` namespace
  (already exists) or per-user namespaces.
- SealedSecrets for service env; ghcr `ghcr-ejbp` pull secret.

## 8. Rollout order (safe → shared, confirm each shared step)

1. Local/safe: CODE_RULES + north-star (done), per-agent internal-git +
   recovery skill in the SDK template, console/website reorientation.
2. OSS: rename repo to agentboom-sdk; move repos to agent-boom org.
3. Infra (confirmed): deploy secrets-api to owl; provision `agentboom`
   Keycloak realm; register clients; create org runner.
4. Product: control plane + per-user runtime chart; billing/llm-gateway
   wiring; Stripe test mode; pricing tiers; disk-quota enforcement.
5. Live: Stripe live, public launch.

## 9. Tenant threat model & abuse prevention (360°)

Core principle: **the user's container is hostile territory.** Anything shipped
inside it (env, tokens, code) is under the user's — and their agent's —
control. Therefore:

1. **No privileged secrets in the tenant container.** The agent never receives
   our service tokens, the control-plane token, billing service tokens, or
   Keycloak client secrets. It gets only a **scoped per-user credential** (the
   user's own Keycloak token / short-lived per-user API token) authorizing
   exactly what the website authorizes for that user. Extracting it yields
   only the user's own rights.
2. **Server-side enforcement, never client.** Entitlements, credit checks,
   disk quotas and model-tier routing are enforced by billing-api +
   llm-gateway + control plane. The agent is a client, not a policy engine; a
   rogue agent can't mint credits or disable billing because those live
   server-side.
3. **BYO-model is a feature, not a hole.** Users may point their agent at
   their own LLM endpoint/key; that stops consuming our credits and is
   supported. The metered path (llm-gateway) requires a valid per-user token +
   credit pre-flight, so our models can't be consumed without credits.
4. **Agent-as-user connector.** A base `agentboom` connector/skill lets the
   agent manage the user's own service (view plan/usage/spend, upgrade/
   downgrade, disk quota, restart) via the same control-plane APIs the console
   uses, scoped to the authenticated user — same permissions as the website.
5. **Cross-tenant isolation.** One namespace + per-user NetworkPolicy
   default-deny; agents can't reach each other's pods/PVCs; shared services
   validate realmId/user on every call.
6. **Detection over prevention for in-container code.** We can't stop a user
   modifying their own container; we detect abuse via metering anomalies +
   audit (secrets-api audit, billing events, llm-gateway usage) and suspend
   per ToS.
7. **Supply-chain / prompt-injection.** External content is data (north star);
   gateway public boundary + sender verification; secrets-api envelope
   encryption; SealedSecrets for infra.

## 10. Base-OS onboarding for a starting user

- First-run flow in the agent: name, timezone/language, join Telegram, explain
  self-evolve (opt-in), and the BYO-model-vs-metered choice.
- Commando "getting started" checklist; lean default skill set (calendar,
  email, reminders, weather, digests).
- Recovery/internal-git active from day one (see §4).
- Website launch wizard: Keycloak login → pick name/region → agent running in
  minutes → link to its dashboard and to the console.

## 11. Locale/category organisation of packages

Packages (connectors first, mini-apps later) carry optional metadata in
`.agentboom-package.json`: `country` (ISO-3166 list, absent/`*` = universal),
`category`, `tags`. Discovery surfaces them; `agentboom packages --country PT`
hides irrelevant foreign packages; `add package` prints an advisory (never
blocking) when a package's country differs from the agent's
`profile.json` country. This lets the registry grow (a PT grocery connector
never bothers a DE user) without forking the registry layout. Implemented in
`registries.discover_packages` + `package_matches_country` (2026-08-28).

## 12. Frontend: agent-generated, design system + theming

- Packages ship **background** capabilities; the *frontend* is generated by
  the agent per user (the dashboard is catalog-driven — apps declare views,
  the UI renders them). A package may ship a *reference* view the agent
  adapts, never a rigid web app that breaks when the design changes.
- The dashboard owns a small **design system** (tokens: colour, type, space,
  radius) the agent can evolve, plus **per-user theming** (a theme object the
  user picks or the agent proposes; stored in settings, applied via tokens).
- Base install includes a **ChatGPT-style chat** with the agent, integrated
  in the user frontend (inspired by the qwen-code serve UI's streaming
  session model), alongside the Commando command center.
- **Telegram onboarding** must be complete for users who know nothing about
  Telegram: the telegram-setup skill walks every step (@BotFather, token,
  allowed users) and finishes the wiring itself.

## 13. Platform-owner ops, user rights, agent freedom

- **Owner analytics (aggregated, privacy-safe).** The control plane records
  *operational* metrics per tenant — signup date, plan, active days, jobs run,
  tokens by tier/category, disk usage, health — never message content or
  personal data. Inspired by adjudica's admin view, adapted: counts and
  activity, not content.
- **Account & data deletion (user-only, website-only).** A button in the
  console callable ONLY by the authenticated user (server-side authz, CSRF
  protection, re-auth + typed confirmation). The agent and admin paths cannot
  trigger it. Deletion cancels billing, then purges the tenant namespace,
  PVCs, secrets-api scope and realm user, leaving a tombstone to avoid
  re-create collisions. Irreversible.
- **Agent freedom.** Perimeter security (public boundary, vault, isolation)
  must not clip in-container evolution: installing MCPs/skills/sub-agents/
  packages and mutating the per-user constitution stay allowed. Inside its
  container the agent is the user's — detection over prevention (§9.6).
- **Persistent but secure.** Per-user PVC for data; code under internal git;
  credentials in secrets-api (envelope-encrypted); encryption at rest; backups
  of the data volume only — never plaintext secrets.

## Open questions (need user decisions)

- Final names (agentboom-sdk / agentboom OS / console).
- Move repos to agent-boom org now vs after the control plane exists.
- Deploy secrets-api to owl now (prerequisite chain) — confirm.
- Per-user namespace vs per-tier namespace.
- Stripe test-only for now (assumed yes).
