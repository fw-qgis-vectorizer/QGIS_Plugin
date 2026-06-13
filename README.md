# FieldWatch Vectorizer (QGIS plugin)

QGIS plugin for **FieldWatch**: AI-assisted vectorization and imagery editing on georeferenced rasters — interactive map segmentation, cloud inference over large areas, AI inpainting edits, and drone imagery ordering.

Works on **QGIS 3.x and 4.x** (Qt6-compatible).

## Opening the plugin

**Plugins → FieldWatch Vectorizer → FieldWatch** (or the toolbar binocular icon).

This opens **The FieldWatch Pack** — a landing page with four workflow buttons and shared footer actions.

---

## The four workflows

| Workflow | What it does | Where it runs |
|----------|----------------|---------------|
| **One-Click Segmentation** | Click roofs on the map; SAM-based masks in QGIS (local model) | Separate window |
| **Large Area Vectorization** | Draw a polygon; cloud API detects buildings or solar panels | Main pack dialog |
| **AI Imagery Edit** | Draw boxes or roof polygons; cloud AI removes obstacles or adds solar panels | Separate window |
| **Order Drone Imagery** | Request orthomosaic, DSM, or point cloud for an AOI | Separate window |

Licence and trial state are **shared across workflows** (same QGIS profile), but each page applies its own rules (see below).

---

## 1. One-Click Segmentation

Interactive segmentation on your raster — left-click to add a roof, right-click to remove a mask. Best for refining individual structures on imagery you already have loaded.

### Open

Landing → **One-Click Segmentation**.

### First-time setup (in the segmentation window)

1. **Install dependencies** — Downloads a portable Python 3.12 runtime (if needed), then installs PyTorch and the model engine into `~/.qgis_fieldwatch/model/venv/`.
2. **Download model** — Fetches model weights from FieldWatch hosting into `~/.qgis_fieldwatch/model/checkpoints/`.

You can also click **Start segmentation** and the plugin will run missing setup steps automatically.

### Licence (required to start)

- **Paid licence** — Enter your key → **Validate** → status shows **✓ Valid** → **Start segmentation** enables.
- **Trial** — Trial runs are auto-activated on first open (server decides). **Start** is enabled only when the server reports **3 trial runs remaining** (full quota). After a trial session is used, remaining runs drop below 3 and **Start** stays disabled until you **Validate a paid licence** (“Add licence key to use.”).

Trial quota on this page is read from **local cache** when you refresh; opening the page performs **one quick server check** (unless a paid licence is already stored).

### Run

1. Add a **raster layer** to the project.
2. Select it in **Input raster layer** → **Refresh** if needed.
3. **Start segmentation** — first launch may take ~1 minute to load the model.
4. Click on the map: **left** = add mask, **right** = remove. **Space** (toggle) + move/trackpad, or **middle mouse drag** to pan.
5. **Save all to layer** — writes polygons to a memory/vector layer (uses trial quota or paid licence as configured).
6. **Stop segmentation** when finished.
7. **Back** returns to the landing page.

---

## 2. Large Area Vectorization

Cloud inference over a **polygon crop** of your raster — building or solar-panel detection at scale. Results come back as vector layers (and optional summary tables) from the FieldWatch API.

### Open

Landing → **Large Area Vectorization**.

### Licence

The licence panel is at the **top of this page**:

- **Validate** a paid licence key, or paste a trial UUID.
- **Trial quota** is fetched from the server when you open the page or click **Refresh** (live status, not cached like one-click).
- **Run** stays disabled until a polygon is drawn **and** you have a valid paid JWT or active trial with runs remaining.

### Run

1. Add a **raster layer** to the project.
2. Choose **Input raster layer** and **Detection type** (Building or Solar panel).
3. **Draw Polygon** on the map (≥3 vertices, finish with **right-click**). The dialog hides while drawing.
4. Set **Output layer name** (default: `Building_Detections`).
5. **Run** — dialog stays open; progress bar shows crop, upload, inference, and download.
6. On success, vector (and optional CSV/summary) layers are added to the project.
7. **Back** returns to the landing page.

**Refresh** resets the crop/UI and refreshes trial state from the server.

---

## 3. AI Imagery Edit

Cloud AI inpainting on **local crops** of your raster — remove obstacles or add solar panels. Only small cropped regions are sent to the server; the full source raster is never uploaded.

### Open

Landing → **AI Imagery Edit**.

### Licence

Same licence panel as other paid workflows — validate a licence key or use trial access with remaining runs.

### Shared controls

- **Input raster layer** — must be georeferenced.
- **Pan while drawing:** **Space** (toggle) + move/trackpad, or middle mouse drag (same as one-click segmentation).
- **Export merged imagery…** — after edits complete, check rows in either tab and save a **new** merged GeoTIFF (source file is not overwritten).

### Tab: Obstacle removal

1. Enter **what to remove or change** (required), or use a preset (Vehicle, Equipment, Tarp, Shadow).
2. Set **padding around box** (metres) — extra context around each crop for cleaner edges.
3. **Draw removal box** — drag rectangles on the map; draw multiple boxes without re-clicking the button.
4. **Remove obstacles** — crops each box locally, sends to FieldWatch `/qgis/edit`, adds patch layers under **AI edits**.
5. A built-in system prompt encourages clean inpainting that matches surrounding areas (not shown in the UI).

### Tab: Solar panels

1. **Draw roof polygon** — left-click vertices, **right-click** to finish.
2. **Optional size / layout hint** — e.g. `Use 1.7 m × 1.0 m panels in 3 rows; fill ~80% of the roof`. Hints are approximate; the AI tries to follow them visually but results are not survey-accurate.
3. Set **padding around area** (metres).
4. **Add solar panels** — sends crop + mask to `/qgis/edit`; patch layers appear under **Solar panels**.

### Results

- Each edit returns a georeferenced GeoTIFF patch layer aligned with the source.
- Original raster is unchanged.
- Overlays clear when processing finishes.
- **Back to Pack** returns to the landing page.

---

## 4. Order Drone Imagery

Submit an area-of-interest to FieldWatch for a **quote and follow-up** on drone capture and deliverables. Does not run inference in QGIS.

### Open

Landing → **Order Drone Imagery** (separate window).

### Define AOI

- **Draw on the map** — **Draw AOI on map**; left-click vertices, right-click to finish; **Clear** to reset.
- **Coordinates** — Under **AOI by coordinates (lat/lng, WGS84)**, add points and **Use points**; **Clear points** to reset.

### Options

- **Resolution (GSD):** 1 cm, 2 cm, or 5 cm.
- **Deliverable:** Orthomosaic, DSM, or point cloud.

A **price estimate** appears after the AOI is set (based on $300/km²).

### Submit

Enter **name** and **email** (phone and company optional), then **Submit request**. FieldWatch will contact you.

No licence key is required for this form.

---

## Landing page (shared)

- **Get Licence** — Opens [usefieldwatch.com](https://usefieldwatch.com/) in your browser.
- **How to use this plugin** — Walkthrough video.
- **Send feedback** — Short form posted to FieldWatch.

---

## Requirements

- QGIS 3.0+ or QGIS 4.x
- Internet access for API calls, licence validation, and (for one-click) first-time dependency/weight downloads
- One-click segmentation: Windows benefits from Python 3.12 wheels; ~300+ MB disk for model weights and venv under `~/.qgis_fieldwatch/model/`
- AI Imagery Edit: georeferenced raster layer; licence or trial with remaining runs

---

## Installation

### Install from ZIP (recommended for end users)

1. Build or obtain the plugin zip (see `package_vec_plugin.ps1` on Windows).
2. QGIS → **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Enable **FieldWatch Vectorizer**.

The zip does **not** include model weights or the Python venv; those download at runtime for one-click segmentation.

### Clone into the QGIS plugins folder

**Windows** — from your profile’s `python\plugins` folder:

```bat
cd %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins
git clone https://github.com/fw-qgis-vectorizer/QGIS_Plugin.git vec_plugin
```

**macOS / Linux** — same path under your active QGIS profile’s `python/plugins/`.

Then enable under **Plugins → Manage and Install Plugins → Installed**.

---

## Packaging for the QGIS plugin repository

From the plugin root on Windows:

```powershell
.\package_vec_plugin.ps1
```

This creates `vec_plugin.zip` containing a `vec_plugin/` folder ready for **Install from ZIP** or official repository submission. Dev files (`__pycache__`, specs, local caches) are excluded.

---

## Troubleshooting

### Plugin not appearing

- Confirm the folder is under `python/plugins/` with `metadata.txt` and `__init__.py`.
- Check `qgisMaximumVersion` in `metadata.txt` for QGIS 4.
- Restart QGIS.

### One-click: dependencies or weights

- Delete `~/.qgis_fieldwatch/model/venv` and retry **Install dependencies**.
- Delete incomplete files in `~/.qgis_fieldwatch/model/checkpoints/` and retry **Download model**.

### One-click: Start disabled

- Validate a **paid licence**, or ensure trial shows **3 runs remaining**.
- Add a raster layer and click **Refresh**.

### Large area / AI edit: Run or Apply disabled

- Draw at least one region (box or polygon) and validate licence or activate trial with remaining runs.
- For obstacle removal, enter a removal prompt.

### AI edit: patch misaligned or failed

- Ensure the raster is georeferenced.
- Reduce box/polygon size or padding if the crop exceeds server limits.
- Check **View → Panels → Log Messages** — filter for `FieldWatch`.

### Processing / API errors

- **View → Panels → Log Messages** — filter for `FieldWatch`.
- Check network, firewall, and proxy settings.

### Polygon / AOI drawing

- At least three vertices; finish with **right-click** on the map canvas.

---

## License

MIT License — see [LICENSE](LICENSE).

## Support

- Website: https://usefieldwatch.com/
- Issues: https://github.com/fw-qgis-vectorizer/QGIS_Plugin

Version and changelog: `metadata.txt`.
