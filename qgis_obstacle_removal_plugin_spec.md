# Agent prompt: Build “Obstacle Removal” UI in FieldWatch QGIS Plugin

You are implementing a **new fourth workflow** in the existing [FieldWatch Vectorizer QGIS plugin](https://github.com/fw-qgis-vectorizer/QGIS_Plugin) (`vec_plugin`). The backend is already live. Your job is **UI + map interaction + local GDAL cropping + HTTP calls only**. Do not change the inference server.

---

## Product summary

**Workflow name (user-facing):** `Obstacle Removal` or `AI Obstacle Removal`

**What it does:** User loads a large georeferenced raster in QGIS (e.g. 1GB ortho). They mark obstacles (vehicles, cranes, tarps, etc.) by drawing **bounding boxes** on the map. They enter a **single shared text prompt** (e.g. “remove the vehicle and reconstruct the ground surface”). On **Apply**, the plugin:

1. Crops each box (+ padding) from the **local** source raster using GDAL
2. POSTs each crop synchronously to the server
3. Receives an **edited, georeferenced GeoTIFF** per crop
4. Adds each edited patch as a **new raster layer** aligned on top of the source

The **full 1GB raster is never uploaded**. Only small crops go to the server. There is **no file_id**, **no GCS upload**, **no job polling**, **no status endpoint** for this workflow.

This is **Option B (crop-in, patch-out)**, not whole-raster editing.

---

## Where it lives in the plugin

Match existing plugin architecture:

| Existing workflow | Pattern to copy |
|---|---|
| Large Area Vectorization | Licence panel, raster layer picker, map draw tool, progress bar, worker thread, API client |
| One-Click Segmentation | Separate window optional, but prefer staying in pack dialog like Large Area |
| Landing page | Add a 4th button on **The FieldWatch Pack** landing page |

**Suggested placement:** New button on landing page → opens dialog (same window as Large Area Vectorization, or a dedicated dialog — follow existing `dialogs/` patterns).

**Licence:** Reuse the **same licence/JWT flow** as Large Area Vectorization:

- `POST /auth/validate` with `{ "license_key": "..." }`
- Store JWT; send `Authorization: Bearer <token>` on every edit request
- Disable **Apply** until licence is valid (paid or trial with runs remaining — follow same rules as Large Area unless product says otherwise)

**Base API URL:** Same config the plugin already uses for inference (default production: `https://inference.usefieldwatch.com`). Do not hardcode if the plugin already has a settings constant.

---

## Backend API (already implemented — do not redesign)

### Endpoint

```
POST {BASE_URL}/qgis/edit
Content-Type: multipart/form-data
Authorization: Bearer {jwt}
```

### Request fields

| Field | Type | Required | Description |
|---|---|---|---|
| `file` | file | **yes** | GeoTIFF crop. Must be georeferenced (CRS + geotransform). |
| `prompt` | string | **yes** | Edit instruction, e.g. `"remove the truck and blend with surrounding surface"` |
| `mask` | string (JSON) | no | GeoJSON **geometry object only** (not a Feature). Type must be `Polygon` or `MultiPolygon`. Limits edit to interior of polygon. |
| `mask_crs` | string | no | EPSG string if mask coords are not in crop CRS, e.g. `"EPSG:4326"`. Omit if mask is already in crop CRS. |
| `model` | string | no | Gemini model override. Default server-side: `gemini-2.5-flash-image`. Leave unset in v1. |

### Response (success)

- **HTTP 200**
- Body: raw edited GeoTIFF bytes
- `Content-Type: image/tiff`
- Headers:
  - `Content-Disposition: attachment; filename="{original_stem}_edited.tif"`
  - `X-Edit-Id: {uuid}`
  - `X-Edit-Model: gemini-2.5-flash-image`
  - `X-Processing-Seconds: "12.34"`

**No JSON body on success.** Save `response.content` directly to a temp `.tif` and load in QGIS.

### Error responses

| Code | Meaning | Show user |
|---|---|---|
| 400 | Missing/invalid prompt, empty file, bad mask JSON | `detail` from JSON body |
| 401 | Invalid/expired JWT | Re-prompt licence validation |
| 413 | Crop > 200MB | “Crop too large — draw a smaller box or reduce padding” |
| 422 | Not georeferenced / not readable GeoTIFF | “Crop must be a georeferenced GeoTIFF” |
| 503 | Server missing `NANO_KEY` | “Edit service unavailable” |
| 500 | Gemini/processing failure | `detail` from JSON body |

FastAPI errors return JSON: `{ "detail": "..." }`.

### Timeouts

Each crop takes ~5–20s. Set HTTP timeout **120s per crop**. Run requests in a **background worker thread** (QThread / existing `workers/` pattern) so QGIS UI stays responsive.

### Auth endpoint (existing)

```
POST {BASE_URL}/auth/validate
Content-Type: application/json
Body: { "license_key": "..." }
Response: { "valid": true, "token": "...", "expires_at": "...", "message": "..." }
```

---

## User workflow (UX spec)

### Step 0 — Preconditions

- User has a **raster layer** in the QGIS project (GeoTIFF or any GDAL-readable georeferenced raster)
- User selects it in **Input raster layer** dropdown (reuse Large Area pattern: combo + Refresh)
- Licence validated

### Step 1 — Shared prompt

Single multiline text field at top of dialog:

- Label: **“What should be removed or changed?”**
- Placeholder: `e.g. Remove the vehicle and reconstruct the ground surface beneath it`
- Required before Apply
- Optional: 3–4 preset chips/buttons: “Remove vehicle”, “Remove construction equipment”, “Remove tarp”, “Remove shadow artifact” — clicking fills the prompt field

**Do not** require a separate prompt per box in v1.

### Step 2 — Draw bounding boxes (multi-select session)

**Map tool:** Rectangle drag on canvas (like QGIS “Select features by rectangle”, not freehand polygon).

Behavior:

1. User clicks **“Draw removal box”** (or tool auto-activates)
2. Drag rectangle over obstacle on map
3. On release: rectangle added to a **list/table** in the dialog; a **semi-transparent red/orange rubber band** stays on map showing all pending boxes
4. Tool **stays active** — user draws box 2, 3, … 10 without re-clicking
5. Dialog hides or docks minimally while drawing (copy Large Area polygon draw UX: dialog hides, user draws, dialog returns)

**List/table columns:**

| # | Label (auto) | Size (approx m or px) | Actions |
|---|---|---|---|
| 1 | Obstacle 1 | 45m × 32m | Remove, Zoom to |
| 2 | Obstacle 2 | … | Remove, Zoom to |

- **Remove** deletes one box from list and map overlay
- **Clear all** button
- Show count: **“3 obstacles selected”**

**Optional per-box override (v1.1, not required v1):** small “custom prompt” expander per row. If empty, use shared prompt.

### Step 3 — Padding control

Slider or spinbox: **Padding around box** (meters, not pixels)

- Default: **~5–10 meters** (convert map units to meters if CRS is geographic — use QgsDistanceArea or project to a metric CRS for display)
- Padding expands the crop sent to server so Gemini has context at edges
- Internally the crop is the **padded bbox**; the **mask** sent to API is the **inner user box** (see crop logic below)

### Step 4 — Apply

Button: **“Remove obstacles”** (disabled until: valid licence + raster selected + ≥1 box + non-empty prompt)

On click:

1. For each box (sequential or limited parallel, max 3 concurrent recommended):
   - Crop padded bbox from source raster → temp GeoTIFF
   - Build mask GeoJSON polygon = **inner box** in crop CRS
   - POST to `/qgis/edit`
   - Save response → temp edited crop
   - Add as raster layer: `{source_layer_name}_edit_{n}` or group layer **“Obstacle removals”**
2. Progress: **“Editing 3 of 10…”** with cancel support (cancel = stop remaining requests, keep completed patches)
3. On full success: message **“10 patches applied. Toggle layers to compare.”**

### Step 5 — Result in QGIS

- Each returned GeoTIFF is **georeferenced** — load with `QgsRasterLayer(path, name)`
- Patches stack **above** source raster; user sees removals immediately
- Do **not** modify the original 1GB layer
- Optional later: “Export merged raster” button (local gdal_merge) — **out of scope v1**

---

## Crop + mask logic (critical — implement exactly)

For each user-drawn rectangle `inner_box` (map coordinates, layer CRS):

### 1. Compute padded crop extent

```
padded_box = inner_box buffered by padding_meters (in layer CRS, or reproject for geographic)
```

Snap padded extent to pixel grid of source raster (use GDAL/rasterio window alignment) so crop edges align with pixel boundaries.

### 2. Write crop GeoTIFF locally

Use GDAL (`gdal.Translate` or rasterio window read + write):

- Output: temp file e.g. `/tmp/fieldwatch_edit_{uuid}_crop.tif`
- Must preserve: CRS, geotransform (adjusted for window origin), band count/dtype
- **Validate crop size < 200MB** before upload; if too large, show error suggesting smaller box or less padding
- Typical obstacle box + 10m padding on 5cm GSD ortho ≈ few MB — fine

### 3. Build mask geometry

Send **`mask`** = GeoJSON geometry of **`inner_box`**, not padded box.

- Coordinates in **crop CRS** (same as cropped GeoTIFF)
- Format: geometry object only:

```json
{
  "type": "Polygon",
  "coordinates": [[[x1,y1],[x2,y2],[x3,y3],[x4,y4],[x1,y1]]]
}
```

- **Do not send `mask_crs`** if coordinates are already in crop CRS (simplest)
- If you build mask in EPSG:4326 from map click, send `mask_crs="EPSG:4326"`

**Why mask matters:** Server edits only inside mask; outside mask pixels are bit-exact original. This makes patches seam cleanly when layered.

**If mask omitted:** Server edits whole crop with feathered borders only — less precise for multi-obstacle workflow. **Always send inner box as mask.**

### 4. POST multipart

```python
files = {"file": (f"crop_{i}.tif", open(crop_path, "rb"), "image/tiff")}
data = {
    "prompt": shared_prompt,
    "mask": json.dumps(inner_box_geojson_geometry),
}
headers = {"Authorization": f"Bearer {jwt}"}
response = requests.post(
    f"{API_BASE}/qgis/edit",
    files=files,
    data=data,
    headers=headers,
    timeout=120,
)
if response.status_code == 200:
    edited_path = save_bytes(response.content, f"edit_{i}.tif")
else:
    err = response.json().get("detail", response.text)
```

---

## UI layout (wireframe)

```
┌─ FieldWatch — Obstacle Removal ─────────────────────────────┐
│ [Licence panel — same as Large Area]                        │
│                                                             │
│ Input raster layer:  [ dropdown ▼ ]  [Refresh]              │
│                                                             │
│ Removal instruction:                                        │
│ ┌─────────────────────────────────────────────────────────┐ │
│ │ Remove the vehicle and reconstruct the ground surface   │ │
│ └─────────────────────────────────────────────────────────┘ │
│ Presets: [Vehicle] [Equipment] [Tarp] [Shadow]             │
│                                                             │
│ Padding: [====●====] 8 m                                    │
│                                                             │
│ Obstacles:  [Draw box on map]  [Clear all]                  │
│ ┌────┬──────────────┬──────────┬─────────┐                  │
│ │ #  │ Area         │ Size     │         │                  │
│ ├────┼──────────────┼──────────┼─────────┤                  │
│ │ 1  │ Obstacle 1   │ 12m×8m   │ [✕][⌖] │                  │
│ │ 2  │ Obstacle 2   │ 9m×9m    │ [✕][⌖] │                  │
│ └────┴──────────────┴──────────┴─────────┘                  │
│ 2 obstacles selected                                      │
│                                                             │
│ [████████████░░░░] Editing 1 of 2…                          │
│                                                             │
│ [ Remove obstacles ]              [ Back to Pack ]          │
└─────────────────────────────────────────────────────────────┘
```

---

## Code structure (follow existing repo)

Implement under existing folders:

```
vec_plugin/
  dialogs/
    obstacle_removal_dialog.py      # main UI
  core/
    obstacle_removal_api.py         # HTTP client for POST /qgis/edit
    obstacle_crop.py                # GDAL crop + mask GeoJSON builder
  workers/
    obstacle_removal_worker.py      # QThread: loop crops, emit progress
  ui/
    obstacle_removal_dialog.ui      # Qt Designer if project uses .ui files
```

**Reuse from Large Area Vectorization:**

- Licence validation widget/logic
- Raster layer combo population
- Map tool pattern (hide dialog → draw → restore dialog)
- Worker + progress bar + error surfacing via `QgsMessageBar` / Log Messages panel tag `FieldWatch`

**Map tool class:** `QgsMapToolEmitPoint` won't work — use `QgsMapToolExtent` or custom rubber-band rectangle tool. Finish on mouse release.

**Store boxes as:** `QgsRectangle` in layer CRS + optional `QUuid` per box for list sync.

---

## Edge cases to handle

| Case | Behavior |
|---|---|
| Non-georeferenced raster | Disable workflow; show “Layer must be georeferenced” |
| Box outside raster extent | Clip crop to raster bounds; warn if inner box mostly outside |
| One crop fails, others succeed | Keep successful patches; report which index failed |
| JWT expires mid-batch | Stop; prompt re-validation; do not silently fail |
| User draws huge box | Pre-check estimated crop MB; block with message before upload |
| Web Mercator layer | Crop in native CRS; mask in same CRS — do not send EPSG:3857 coords as EPSG:4326 without `mask_crs` |
| Multi-band (RGB, RGBA, 4-band) | GDAL crop all bands; server handles ≥3 bands |
| Cancel mid-batch | Worker sets cancel flag; skip remaining POSTs |

---

## What NOT to build

- No upload of full raster for file_id
- No `/qgis/status/{job_id}` polling for this workflow
- No `/qgis/download/geotiff/{job_id}` — response IS the file
- No LaMa / local inpainting
- No SAM3 auto-segmentation in v1 (future: point-click + SAM mask)
- No server-side merge of patches into one output file in v1
- No zip-of-many-tifs response — one request per crop, one tif back

---

## Acceptance criteria

1. Landing page shows **Obstacle Removal** workflow entry
2. User selects georeferenced raster, validates licence, draws ≥2 boxes, enters one prompt, clicks Apply
3. Plugin sends **N synchronous POSTs** (one per box), never uploads full source raster
4. **N edited GeoTIFF layers** appear, aligned with source; obstacles visibly edited in patch areas
5. Original raster layer unchanged
6. Progress shows `Editing i of N`; errors show server `detail` message
7. Works on QGIS 3.x and 4.x (Qt6-compatible — match existing plugin constraint)
8. Log Messages tag `FieldWatch` logs each request: crop size, status code, `X-Processing-Seconds`

---

## Reference: server-side behavior (for debugging)

Server (`nano_edit.py`):

- Gemini receives PNG derived from crop; returns edited PNG
- Server re-attaches original CRS/transform/dtypes → output GeoTIFF
- Masked pixels: edited + feathered blend; unmasked: bit-exact source pixels
- Default model: `gemini-2.5-flash-image`
- Max upload: 200MB per crop (`NANO_EDIT_MAX_UPLOAD_MB`)
- Max dimension to Gemini: 2048px long edge (server downscales/upscales internally)

---

## Example manual test (curl)

```bash
TOKEN="..."  # from /auth/validate
curl -X POST "https://inference.usefieldwatch.com/qgis/edit" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@crop.tif;type=image/tiff" \
  -F "prompt=remove the truck and reconstruct the ground" \
  -F 'mask={"type":"Polygon","coordinates":[[[500020,4499950],[500050,4499950],[500050,4499980],[500020,4499980],[500020,4499950]]]}' \
  -o edited.tif -D -
# Expect: HTTP 200, Content-Type: image/tiff
```

---

## Summary for the implementing agent

Build this workflow end-to-end in the FieldWatch QGIS plugin, matching existing code style, licence flow, and worker patterns. Prioritize clarity and reliability over fancy UI. Ship v1 with **rectangle boxes + single shared prompt + sequential crop POSTs + layered patch results**.
