# FieldWatch Vectorizer (QGIS plugin)

QGIS plugin for **FieldWatch**: AI-assisted vectorization on raster imagery — interactive map segmentation, cloud inference over large areas, and drone imagery ordering.

Works on **QGIS 3.x and 4.x** (Qt6-compatible).

## Opening the plugin

**Plugins → FieldWatch Vectorizer → FieldWatch** (or the toolbar binocular icon).

This opens **The FieldWatch Pack** — a landing page with three workflow buttons and shared footer actions.

---

## The three workflows

| Workflow | What it does | Where it runs |
|----------|----------------|---------------|
| **One-Click Segmentation** | Click roofs on the map; SAM-based masks in QGIS (local model) | Separate window |
| **Large Area Vectorization** | Draw a polygon; cloud API detects buildings or solar panels | Main pack dialog |
| **Order Drone Imagery** | Request orthomosaic, DSM, or point cloud for an AOI | Separate window |

Licence and trial state are **shared across workflows** (same QGIS profile), but each page applies its own rules (see below).

---

## 1. One-Click Segmentation

Interactive segmentation on your raster — left-click to add a roof, right-click to remove a mask. Best for refining individual structures on imagery you already have loaded.

### Open

Landing → **One-Click Segmentation**.

### First-time setup (in the segmentation window)

1. **Install dependencies** — Downloads a portable Python 3.12 runtime (if needed), then installs PyTorch and the model engine into `~/.qgis_fieldwatch/model/venv/`.
2. **Download model** — Fetches `model` weights from FieldWatch hosting into `~/.qgis_fieldwatch/model/checkpoints/`.

You can also click **Start segmentation** and the plugin will run missing setup steps automatically.

### Licence (required to start)

- **Paid licence** — Enter your key → **Validate** → status shows **✓ Valid** → **Start segmentation** enables.
- **Trial** — Trial runs are auto-activated on first open (server decides). **Start** is enabled only when the server reports **3 trial runs remaining** (full quota). After a trial session is used, remaining runs drop below 3 and **Start** stays disabled until you **Validate a paid licence** (“Add licence key to use.”).

Trial quota on this page is read from **local cache** when you refresh; opening the page performs **one quick server check** (unless a paid licence is already stored).

### Run

1. Add a **raster layer** to the project.
2. Select it in **Input raster layer** → **Refresh** if needed.
3. **Start segmentation** — first launch may take ~1 minute to load the model.
4. Click on the map: **left** = add mask, **right** = remove. **Space + drag** or middle mouse to pan.
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

## 3. Order Drone Imagery

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

---

## Installation

### Install from ZIP (recommended for end users)

1. Build or obtain `vec_plugin.zip` (see `package_vec_plugin.ps1` on Windows).
2. QGIS → **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Enable **FieldWatch Vectorizer**.

The zip does **not** include weights or the Python venv; those download at runtime for one-click segmentation.

### Clone into the QGIS plugins folder

**Windows** — from your profile’s `python\plugins` folder:

```bat
cd %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins
git clone https://github.com/fw-qgis-vectorizer/QGIS_Plugin.git vec_plugin
```

**macOS / Linux** — same path under your active QGIS profile’s `python/plugins/`.

Then enable under **Plugins → Manage and Install Plugins → Installed**.

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

### Large area: Run disabled

- Draw a polygon and validate licence or activate trial with remaining runs.

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
