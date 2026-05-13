# QGIS trial API — endpoint design and server logic

## Scope

This document specifies **HTTP JSON** endpoints under the inference API base (e.g. `https://inference.usefieldwatch.com/`) with paths:

- **`GET /qgis/trial/...`**
- **`POST /qgis/trial/...`**

There is **no** `/v1` prefix. Existing paid license flow remains **`POST /auth/validate`** (unchanged contract with the plugin).

This document defines **endpoint names, request/response shapes, state machine, server decision logic, idempotency, errors, and how this coexists with paid JWT inference**.

---

## Concepts

### `install_key` (client-originated, stable)

- Generated **once** by the QGIS plugin on first need, typically a random UUID string.
- Persisted in **`QSettings`** for that QGIS profile.
- Sent on every trial call so the server can recognize “the same install” even if `trial_id` is missing locally.
- **Not secret** (treat as an identifier), but avoid logging full value at high volume if possible.

### `trial_id` (server-originated, opaque)

- Created only when the server **inserts** a new trial row.
- Returned to the client; client persists in `QSettings`.
- Unguessable (e.g. 128-bit+ random, UUID ok).

### “New trial”

Server definition: **a new DB row is inserted** with a fresh `trial_id` and initial `uses_remaining = TRIAL_USES_TOTAL` (default **3**).

### “Billable use”

A **single user-intended inference run** (or whatever product defines). Enforcement is **`POST /qgis/trial/usage`**, called **immediately before** starting billable work.

### Hybrid client behavior (reference)

- **On open / periodically:** call **read-only** trial state endpoint (no decrement).
- **Before each billable action:** call **usage** endpoint (atomic decrement / deny + idempotent retries).

---

## Coexistence with paid license (`POST /auth/validate`)

**Rule (recommended):**

- If the user has a **valid paid entitlement JWT** active for inference, the plugin **does not** call `/qgis/trial/usage` for that run.
- If the user is on **trial mode** (no paid JWT / trial tier JWT / explicit trial flag—pick one representation), the plugin **must** call `/qgis/trial/usage` before billable work.

If the system distinguishes modes via JWT claims, specify claims such as:

- `entitlement: "paid" | "trial" | "none"`
- or `trial_id` embedded in JWT for trial-tier tokens

**Minimum viable approach without JWT changes:**

- Plugin tracks mode locally: “paid validated” vs “trial active”.
- Server should still enforce trial limits on trial-tier inference if trial users can hit inference endpoints with some token—**prefer** aligning inference auth with server-side entitlement checks.

---

## Endpoint A — read-only trial resolution + bootstrap

### `GET /qgis/trial/state`

**Purpose:** Return current trial state for UI. **Must never decrement** `uses_remaining`. May **create** a trial only when allowed and no existing mapping exists.

**Why GET:** read-only semantics; safe to retry; use `Cache-Control: no-store` in practice.

### Query parameters

| Name | Required | Description |
|------|----------|-------------|
| `install_key` | **yes** | stable per-install identifier |
| `trial_id` | no | if client has it |
| `plugin_version` | no | telemetry/support |
| `qgis_version` | no | telemetry/support |

If `install_key` is missing → `400`.

### Response `200` JSON

| Field | Type | Description |
|------|------|-------------|
| `trial_id` | string | canonical id |
| `uses_total` | int | default 3 |
| `uses_remaining` | int | current remaining |
| `trial_state` | string | `active` \| `exhausted` \| `revoked` |
| `server_time` | string | ISO-8601 |

### Server logic (exact decision order)

Implement as a single conceptual transaction (serialized per `install_key` to avoid double-create races):

1. **Normalize inputs** (trim strings; max lengths; reject absurd sizes).

2. **If `trial_id` provided**

   - Lookup trial by `trial_id`.
   - If found:
     - **Verify binding** to `install_key`:
       - If trial row has stored `install_key` and it **does not match** request `install_key` → `403` `INSTALL_KEY_MISMATCH` (prevents trial token swapping between machines/profiles).
       - If trial row has **no** `install_key` yet (migration path) → optionally bind now (one-time) if policy allows; otherwise apply mismatch rules.
     - Return state (**no decrement**).
   - If not found: treat `trial_id` as invalid/stale → continue to step 3 (do **not** auto-insert solely based on bad `trial_id`).

3. **Lookup by `install_key`**

   - If a trial exists for `install_key`:
     - Return canonical `trial_id` + state (**no decrement**).
     - If client sent a different `trial_id` than canonical, **return canonical** `trial_id` (client should overwrite local storage).

4. **No trial exists for `install_key` → potential NEW trial**

   Apply **creation policy**:

   - Global feature flag enabled?
   - Rate limits: per IP / per ASN / per hour/day thresholds (soft anti-abuse)?
   - Optional: deny if signals indicate obvious automation (document as best-effort)

   If allowed:

   - `INSERT` trial:
     - `trial_id` new random
     - `install_key` set
     - `uses_total = TRIAL_USES_TOTAL`
     - `uses_remaining = TRIAL_USES_TOTAL`
     - `trial_state = active`
   - Return `200` with fresh state.

   If not allowed:

   - Return `429` or `403` with structured error. **No insert**.

### Caching / performance headers

- Respond with `Cache-Control: no-store`.

### Errors (structured JSON body)

All errors use:

```json
{
  "error_code": "SOME_CODE",
  "message": "human readable",
  "retry_after_seconds": 0
}
```

| HTTP | `error_code` | When |
|------|----------------|------|
| `400` | `MISSING_INSTALL_KEY` | missing/empty |
| `403` | `INSTALL_KEY_MISMATCH` | `trial_id` belongs to different install |
| `403` | `TRIALS_DISABLED` | global off |
| `429` | `RATE_LIMITED` | trial creation throttled |
| `500` | `INTERNAL` | unexpected |

---

## Endpoint B — atomic billable usage authorization (decrement / deny)

### `POST /qgis/trial/usage`

**Purpose:** Authorize exactly **one** billable use for a trial, **atomically** decrementing `uses_remaining` when allowed. Must support **idempotent retries**.

### Request JSON body

| Field | Type | Required | Description |
|------|------|----------|-------------|
| `trial_id` | string | **yes** | |
| `install_key` | string | **yes** | must match trial binding |
| `idempotency_key` | string | **yes** | unique per user-initiated run |
| `action` | string | no | default `infer` |
| `plugin_version` | string | no | |
| `qgis_version` | string | no | |

### Idempotency_key (client contract)

- Generate **new UUID** per click/run.
- Reuse the same key only on **retries of the same run** (network retry, user double-submit if deduped).

### Response `200` JSON — allowed

```json
{
  "allowed": true,
  "uses_remaining": 2,
  "receipt": "opaque_optional_string"
}
```

### Response `200` JSON — denied (trial exhausted)

**Style 1** (always HTTP 200, simplest client parsing):

```json
{
  "allowed": false,
  "uses_remaining": 0,
  "error_code": "TRIAL_EXHAUSTED",
  "message": "Trial uses exhausted. Register to obtain a full license key."
}
```

**Style 2:** HTTP `402` or `409` with the same JSON body — clearer in logs, slightly more client branching.

### Server logic (must be transactional)

Use a DB transaction with row lock on the trial row **or** an equivalent atomic mechanism.

1. Validate body: missing fields → `400`.

2. **Idempotency lookup** first:

   - Table conceptually: `trial_usage_idempotency(trial_id, idempotency_key) -> outcome`
   - If record exists:
     - Return **exactly the same** response as originally stored (`allowed`, `uses_remaining_after`, receipt, error fields).
     - **Must not** decrement again.

3. Load trial by `trial_id` **FOR UPDATE** (lock row).

4. Verify `install_key` matches trial’s stored `install_key`. Mismatch → `403` `INSTALL_KEY_MISMATCH` (and do not create idempotency success that looks like a consume).

5. If `trial_state != active` → deny (`allowed=false`, `TRIAL_REVOKED` / appropriate code).

6. If `uses_remaining <= 0` → deny (`TRIAL_EXHAUSTED`).

7. Else decrement:

   - `uses_remaining -= 1`
   - if becomes 0 → set `trial_state=exhausted` (recommended at last decrement for simpler queries)

8. Insert idempotency record capturing final outcome.

9. Commit.

**Receipt (optional):** random opaque string stored with idempotency row for support correlation.

### Errors (non-200 if strict HTTP semantics)

| HTTP | `error_code` | When |
|------|----------------|------|
| `400` | `MISSING_FIELDS` | missing trial_id/install_key/idempotency_key |
| `403` | `INSTALL_KEY_MISMATCH` | |
| `404` | `TRIAL_NOT_FOUND` | unknown trial_id |
| `409` | `CONFLICT` | rare internal conflict; clients should retry with same idempotency key |

For transient server errors, clients should retry **`POST /qgis/trial/usage`** with the **same** `idempotency_key` to avoid double charging.

---

## Endpoint C (optional)

### `POST /qgis/trial/event`

Only include if separate analytics from `state` is required. **Must not** decrement uses; rate-limited; non-authoritative.

If not needed, **omit** this endpoint.

---

## State machine

### `trial_state`

- **`active`:** `uses_remaining > 0` and not revoked  
- **`exhausted`:** `uses_remaining == 0`  
- **`revoked`:** admin abuse / fraud / support action  

### Transitions

- nonexistent → `active` on successful create (`GET /qgis/trial/state` insert path)
- `active` → `exhausted` on successful final decrement
- `*` → `revoked` manual/admin

---

## Race conditions and consistency

### Double creation from parallel `GET /qgis/trial/state`

Mitigate with:

- **Unique DB constraint** on `install_key`
- Insert using “insert or on conflict return existing” pattern  

So two concurrent GETs for the same new install converge to **one** trial row.

### Double spend

Mitigated by **`idempotency_key`** on `POST /qgis/trial/usage`.

---

## Security and abuse expectations

- `install_key` is client-controlled; determined users can reset by wiping the QGIS profile → new `install_key` → may obtain another anonymous trial unless **account binding**, captcha, payment, etc. are added.
- This design stops **casual** “close/reopen” resets and accidental duplicates.
- `trial_id` must be unguessable.

---

## Client integration contract (plugin)

### Keys persisted in `QSettings`

- `install_key` (create if missing)
- `trial_id` (overwrite whenever server returns canonical)

### Call sequence

1. Ensure `install_key` exists locally.
2. `GET /qgis/trial/state?install_key=...&trial_id=...` — update UI; persist returned `trial_id`.
3. On billable action:
   - If paid mode: skip usage endpoint per coexistence rule.
   - Else: `POST /qgis/trial/usage` with `{ trial_id, install_key, idempotency_key }` — if `allowed=false`, block run and show upgrade CTA.

### Timeouts

- `state`: short timeout (2–3s) for UI refresh; failures are non-fatal to opening the dialog.
- `usage`: stricter; on transient failure, retry with the same `idempotency_key`.

---

## Backend deliverables checklist

1. Implement **`GET /qgis/trial/state`** (no decrements).  
2. Implement **`POST /qgis/trial/usage`** with **DB transactions**, **row locking**, and **idempotency** table.  
3. Document OpenAPI examples for both endpoints + error catalog.  
4. Configure **`TRIAL_USES_TOTAL=3`** as server config.


>> ## PLUGIN UI & INTEGRATION AGENT — READ THIS SECTION FIRST
>
> **Handoff for QGIS plugin UI builders and coding agents.** Trial = trial API + three HTTP headers on QGIS routes; paid = `POST /auth/validate` + Bearer only. If `Authorization` is present and invalid, the server returns **401** and does **not** fall back to trial headers — for trial traffic, **omit** Bearer.

### How end users know the trial cap (no hardcoded “3” in the plugin)

The server always reports the cap in JSON:

- **`GET /qgis/trial/state`** returns **`uses_total`** and **`uses_remaining`** (and `trial_state`). Default cap is configured on the server (`TRIAL_USES_TOTAL`, often **3**), but the UI should **display the numbers from the API**, not assume a constant.
- **`POST /qgis/trial/usage`** returns **`uses_remaining`** on every **200** (allowed or denied). When exhausted, **`allowed`: false** and **`error_code`: `TRIAL_EXHAUSTED`** (with message).

So users are **not** dependent on a separate “3-call rule” document: the plugin must **refresh state** and **show remaining uses** from these fields.

### What is *not* implemented (vs earlier spec table)

- **`429` / `RATE_LIMITED` on *new trial creation*** (anti-abuse when many fresh `install_key`s hit bootstrap) is **not** implemented on the backend today. That does **not** hide the per-trial use cap — it only affects bulk *creation* throttling. Mitigation otherwise: unique `install_key` per install, normal ops monitoring, WAF / edge rate limits if you need them.

```
╔══════════════════════════════════════════════════════════════════════════╗
║ PLUGIN UI & INTEGRATION AGENT — READ THIS SECTION FIRST (BANNER)         ║
║ Trial: trial API + X-Trial-Receipt / X-Trial-Install-Key / X-Trial-Id     ║
║ Paid: POST /auth/validate + Bearer — omit Bearer when using trial         ║
╚══════════════════════════════════════════════════════════════════════════╝
```

<div align="center">

**PLUGIN UI & INTEGRATION AGENT — READ THIS BLOCK**

| |
|:--|
| **Trial mode:** no long-lived JWT. Call trial endpoints, then QGIS routes with **`X-Trial-Receipt`**, **`X-Trial-Install-Key`**, **`X-Trial-Id`**. |
| **Paid mode:** `POST /auth/validate` → store JWT → QGIS routes with **`Authorization: Bearer …`** only; skip **`POST /qgis/trial/usage`**. |
| **Invalid Bearer:** returns **401**; trial headers are ignored. **Omit Bearer** for trial. |

</div>

---

## Appendix — Plugin UI & agent integration (implementation contract)

Use this with the earlier sections (“Concepts”, “Client integration contract”, errors, idempotency). Base URL: same host as inference (e.g. `https://inference.usefieldwatch.com`), **no `/v1` prefix**.

### Modes at a glance

| Mode | License step | Billable gate | QGIS infer / panel / status / download |
|------|----------------|-----------------|----------------------------------------|
| **Paid** | `POST /auth/validate` → store JWT | No `POST /qgis/trial/usage` | `Authorization: Bearer <JWT>` |
| **Trial** | None (no JWT) | `POST /qgis/trial/usage` **immediately before** each run | Three headers (below); **omit** `Authorization` |

### Persist in `QSettings` (or equivalent)

| Key | Rule |
|-----|------|
| `install_key` | Create once (e.g. UUID); send on every trial call. |
| `trial_id` | Server canonical; **overwrite** whenever `GET /qgis/trial/state` returns `trial_id`. |
| (optional) last `receipt` | Only valid until first successful enqueue for that run; you may discard after job is queued. |

### Trial API (public to the plugin; DB-backed on server)

1. **`GET /qgis/trial/state?install_key=<required>&trial_id=<optional>&plugin_version=<optional>&qgis_version=<optional>`**  
   - Read-only for quota; **never** decrements.  
   - Response **`200`** JSON: `trial_id`, **`uses_total`**, **`uses_remaining`**, `trial_state` (`active` \| `exhausted` \| `revoked`), `server_time`.  
   - Response header: **`Cache-Control: no-store`**.  
   - Errors: JSON body `error_code`, `message`, `retry_after_seconds` (e.g. `MISSING_INSTALL_KEY`, `INSTALL_KEY_MISMATCH`, `TRIALS_DISABLED`).  
   - **Note:** `429` / `RATE_LIMITED` from the original design doc is **not** returned for trial bootstrap in the current server build.

2. **`POST /qgis/trial/usage`**  
   - Body JSON: `trial_id`, `install_key`, `idempotency_key` (**required**); optional `action` (default `infer`), `plugin_version`, `qgis_version`.  
   - **New UUID per user click** for `idempotency_key`; reuse **only** on retries of the **same** run.  
   - **`200`** when allowed: `allowed: true`, **`uses_remaining`**, **`receipt`**.  
   - **`200`** when denied (e.g. exhausted): `allowed: false`, **`error_code`** (e.g. `TRIAL_EXHAUSTED`), `message`, **`uses_remaining`**.  
   - Idempotent replays return the **same** JSON as the first response for that `(trial_id, idempotency_key)`.

### QGIS job endpoints — trial (Option B: receipt + install + trial id)

After a successful **`POST /qgis/trial/usage`**, call infer/panel with **all** of these headers (names exact, case as shown):

| Header | Value |
|--------|--------|
| `X-Trial-Receipt` | `receipt` from the usage response |
| `X-Trial-Install-Key` | same `install_key` as trial APIs |
| `X-Trial-Id` | same `trial_id` as trial APIs |

**Do not** send `Authorization: Bearer` on these requests if you intend trial auth (invalid Bearer wins and returns **401**).

**Endpoints:**

- `POST /qgis/infer/{file_id}` — query params unchanged (prompt, confidence, etc.).  
- `POST /qgis/panel/{file_id}` — same pattern.  
- Server consumes the receipt **once** when the job is accepted and binds it to the returned **`job_id`**.

**Poll and download (trial):** same three headers on every request:

- `GET /qgis/status/{job_id}`  
- `GET /qgis/download/shapefile/{job_id}`  
- `GET /qgis/download/json/{job_id}`  

The server checks that the receipt row is **consumed** and **`job_id`** matches the URL.

**Receipt errors (typical):** `TRIAL_RECEIPT_INVALID`, `TRIAL_RECEIPT_USED`, `INSTALL_KEY_MISMATCH` — JSON body with `error_code` / `message` on infer/panel when returned as structured error responses.

### QGIS job endpoints — paid (unchanged contract)

- Send **`Authorization: Bearer <JWT>`** from `POST /auth/validate`.  
- No trial usage step; no `X-Trial-*` headers required.

### UI / agent behavior checklist

1. On dialog open (or periodic refresh): **`GET /qgis/trial/state`** — show **`uses_remaining` / `uses_total`** and `trial_state`; persist `trial_id`.  
2. On **trial** “Run” (billable): generate **`idempotency_key`** → **`POST /qgis/trial/usage`** → if `allowed` false, show message (e.g. `TRIAL_EXHAUSTED`) and upgrade CTA; else update **`uses_remaining`** from the response.  
3. If `allowed` true: **`POST /qgis/infer/...` or `POST /qgis/panel/...`** with the **three trial headers** (no Bearer).  
4. Poll **`GET /qgis/status/{job_id}`** with the **same three headers** until terminal state.  
5. Download artifacts with the **same three headers**.  
6. **Timeouts:** short for `state` (non-fatal if it fails); stricter for `usage` and infer; on transient failure retry **`usage`** with the **same** `idempotency_key`.  
7. **503** on trial routes if the server has no working trial DB (`DATABASE_URL` / init failure) — show “trials unavailable”.

### Server env the plugin does not set (for ops / support)

- `DATABASE_URL` — required for trials.  
- `TRIAL_USES_TOTAL` (default `3`), `TRIAL_RECEIPT_TTL_SECONDS` (receipt lifetime, default `300`), `QGIS_TRIALS_ENABLED`.

### Running automated tests (trial store / Postgres)

From the `sam/` directory, with **`PYTEST_DATABASE_URL`** or **`DATABASE_URL`** set to a Postgres URL the tests can write to:

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest tests/test_trial_store.py -v
```

Tests use `install_key` values prefixed with `pytest_trial_` so they are easy to spot in the database. If neither env var is set, Postgres-backed tests **skip** automatically. `tests/conftest.py` loads **`/home/ayofalowo/sam/.env`** first (or set **`SAM_ENV_FILE`**), then falls back to `sam/.env` next to the repo.