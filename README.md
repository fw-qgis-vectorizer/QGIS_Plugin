# FieldWatch Vectorizer (QGIS plugin)

QGIS plugin for **FieldWatch** vectorization: run AI segmentation on your raster imagery to produce building or solar-panel masks, integrated with the FieldWatch inference API.

## Features

- **Polygon-based area selection** — Draw a polygon on the map to define the crop area for inference.
- **Building and solar-panel detection** — Choose **Detection type** (e.g. Building or Solar panel) before running.
- **Paid license and trial** — Enter a **paid license key** and **Validate** for JWT-backed runs, or use the **trial** flow (install key, trial UUID, server-backed quota). The dialog shows **trial quota** and a single control to **Generate Trial Key** or open FieldWatch for a full licence, depending on state.
- **Automated pipeline** — Crops/compresses the raster, uploads, runs inference, downloads results, and loads the vector layer (and optional summary table when the API returns one).
- **Progress and status** — Progress bar and status text during processing.
- **Run / Cancel / Refresh** — **Run** starts processing while keeping the dialog open; **Cancel** closes the dialog; **Refresh** resets crop/UI progress and refreshes trial state from the server.
- **Order drone imagery** — Request orthomosaic, DSM, or point cloud for an AOI (drawn on a map or from coordinates), with resolution, estimate, and contact submission to FieldWatch.
- **Help and feedback** — **How to use this plugin** opens the walkthrough video; **Send feedback** opens a short form that posts feedback to FieldWatch.

## Requirements

- QGIS 3.x  
- Python 3.x (bundled with QGIS)  
- Internet access for API calls  

## Installation

### Clone into the QGIS plugins folder (recommended)

The repository root **is** the plugin package (it contains `metadata.txt`, `__init__.py`, etc.). Clone it so the directory name under `python/plugins/` matches how you manage plugins (often `vec_plugin`):

**Windows (Command Prompt or PowerShell)** — from your profile’s `python\plugins` folder (open it via **Settings → User Profiles → Open Active Profile Folder**, then enter `python\plugins`):

```bat
cd %APPDATA%\QGIS\QGIS3\profiles\default\python\plugins
git clone https://github.com/fw-qgis-vectorizer/QGIS_Plugin.git vec_plugin
```

**macOS / Linux** — use the same path inside your active QGIS profile’s `python/plugins` directory, then:

```bash
git clone https://github.com/fw-qgis-vectorizer/QGIS_Plugin.git vec_plugin
```

Then enable the plugin under **Plugins → Manage and Install Plugins → Installed** (search for **FieldWatch Vectorizer**).

### Or copy manually

1. Open **Settings → User Profiles → Open Active Profile Folder** in QGIS.  
2. Go to `python/plugins/`.  
3. Place this plugin as a single folder (for example `vec_plugin/`) containing `__init__.py`, `metadata.txt`, `vec_plugin.py`, and the rest of the files from this repository.

### Install from ZIP (QGIS)

Zip the plugin folder so the archive contains one top-level folder (e.g. `vec_plugin/...`). In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.

## Usage

1. Add a suitable **raster layer** to the project.  
2. Open **Plugins → FieldWatch Vectorizer → FieldWatch** (toolbar: same action).  
3. **License** — Validate a paid key and/or use the trial controls as prompted; confirm trial quota if you are not on a paid JWT.  
4. Choose the **input raster** and **detection type**.  
5. Click **Draw Polygon**, finish with **right-click** (at least three vertices).  
6. Set an **output layer name** if you want (default: `Building_Detections`).  
7. Click **Run** — the dialog stays open while processing; when finished, layers are added and the map may zoom to results.  
8. Use **Cancel** to close, or **Refresh** to reset the dialog and refresh trial state.

## Order drone imagery

Use **Plugins → FieldWatch Vectorizer → Order drone imagery**.

### AOI

- **Draw on the map** — **Draw AOI on map**; left-click vertices, right-click to finish; **Clear** to reset.  
- **Coordinates** — Under **AOI by coordinates (lat/lng, WGS84)**, add points and **Use points**; **Clear points** to reset.

### Options

- **Resolution (GSD):** 1 cm, 2 cm, or 5 cm.  
- **Deliverable:** Orthomosaic, DSM, or point cloud.  

After the AOI is set, a **price estimate** is shown (based on $300/km²).

### Contact and submit

Provide **name** and **email** (phone and company optional), then **Submit request**. FieldWatch will follow up.

## Troubleshooting

### Plugin not appearing

- Confirm the folder lives under `python/plugins/` with all required files.  
- Restart QGIS; check **Manage and Install Plugins → Installed**.

### Processing errors

- Open **View → Panels → Log Messages** and filter for plugin messages.  
- Check connectivity and that the raster is valid.

### Polygon drawing

- Need at least three vertices before finishing with right-click.  
- The main dialog hides while drawing; finish on the map canvas.

### Upload timeout

- Large crops can take several minutes (long client timeout).  
- Reduce area if uploads fail or time out.

## License

This plugin is licensed under the **MIT License**. See the [LICENSE](LICENSE) file.

## Support

Issues and contributions:  
https://github.com/fw-qgis-vectorizer/QGIS_Plugin

## Version

See `metadata.txt` for the current version and changelog.
