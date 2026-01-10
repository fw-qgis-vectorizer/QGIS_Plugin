# VEC Plugin - QGIS Plugin for Building Detection

A QGIS plugin that integrates with the VEC inference service for automated building detection from raster imagery using AI segmentation.

## Features

- **Polygon-based Area Selection**: Draw a polygon on the map to select the specific area for building detection
- **Automated Processing Pipeline**: Uploads raster data, runs AI inference, and downloads results automatically
- **Progress Tracking**: Real-time progress updates during processing
- **Automatic Layer Loading**: Results are automatically loaded into QGIS as a vector layer

## Requirements

- QGIS 3.x
- Python 3.x (included with QGIS)
- Internet connection (for API communication)

## Installation

### Method 1: Manual Installation (Recommended)

1. **Download the Plugin**
   - Clone or download this repository:
     ```bash
     git clone https://github.com/fw-qgis-vectorizer/QGIS_Plugin.git
     ```
   - Or download as ZIP from GitHub and extract it

2. **Locate QGIS Plugin Directory**
   - Open QGIS
   - Go to **Settings → User Profiles → Open Active Profile Folder**
   - Navigate to `python/plugins/` directory
   - On Windows, this is typically:
     ```
     C:\Users\<YourUsername>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\
     ```

3. **Copy Plugin Files**
   - Copy the entire `vec_plugin` folder to the `python/plugins/` directory
   - The folder structure should be:
     ```
     python/plugins/vec_plugin/
       ├── __init__.py
       ├── vec_plugin.py
       ├── vec_plugin_dialog.py
       ├── vec_plugin_dialog_base.ui
       ├── vec_inference_client.py
       ├── resources.py
       ├── metadata.txt
       └── ... (other files)
     ```

4. **Enable the Plugin**
   - In QGIS, go to **Plugins → Manage and Install Plugins**
   - Click on **Installed** tab
   - Search for "VEC Plugin" or "vec_plugin"
   - Check the checkbox to enable it
   - The plugin should now appear in your Plugins menu

### Method 2: Using Plugin Manager (If Available)

If the plugin is published to the QGIS Plugin Repository:

1. Open QGIS
2. Go to **Plugins → Manage and Install Plugins**
3. Search for "VEC Plugin"
4. Click **Install Plugin**
5. Enable it from the **Installed** tab

## Usage

1. **Load a Raster Layer**
   - Add a raster layer (GeoTIFF, JPEG, etc.) to your QGIS project
   - Ensure the raster contains imagery suitable for building detection

2. **Open the Plugin**
   - Go to **Plugins → VEC Plugin** (or use the toolbar icon if available)
   - The plugin dialog will open

3. **Select Input Layer**
   - Choose your raster layer from the dropdown menu

4. **Draw a Polygon**
   - Click **"Draw Polygon"** button
   - The dialog will hide and you can interact with the map
   - Left-click on the map to add points to your polygon
   - Right-click to finish the polygon (requires at least 3 points)
   - The dialog will reappear showing the selected area

5. **Set Output Name** (Optional)
   - Enter a name for the output layer (default: "Building_Detections")

6. **Run Processing**
   - Click **OK** to start processing
   - The plugin will:
     - Crop the raster to your polygon area
     - Compress and upload to the inference service
     - Run AI building detection
     - Download and load results into QGIS

7. **View Results**
   - The detected buildings will appear as a new vector layer
   - The map will automatically zoom to the results
   - Check the QGIS message bar for processing status

## Configuration

The plugin uses hardcoded service endpoints:
- **Upload Service**: `https://upload.usefieldwatch.com`
- **Inference Service**: `https://inference.usefieldwatch.com`

These are configured in the code and cannot be changed via the UI.

## Troubleshooting

### Plugin Not Appearing
- Ensure the plugin folder is in the correct `python/plugins/` directory
- Check that all required files are present
- Restart QGIS after installation
- Check **Plugins → Manage and Install Plugins → Installed** tab

### Processing Errors
- Check the **QGIS Log Messages Panel** (View → Panels → Log Messages)
- Filter by "VEC Plugin" to see detailed error messages
- Ensure you have an internet connection
- Verify the raster layer is valid and contains imagery

### Polygon Drawing Issues
- Ensure you have at least 3 points before right-clicking to finish
- Make sure the map canvas is visible (dialog may hide during drawing)
- Try clicking "Clear" and redrawing if needed

### Upload Timeout
- Large files may take several minutes to upload
- The timeout is set to 600 seconds (10 minutes)
- For very large areas, consider drawing a smaller polygon

## License

This plugin is licensed under the MIT License. See [LICENSE](LICENSE) file for details.

## Support

For issues, questions, or contributions, please visit:
https://github.com/fw-qgis-vectorizer/QGIS_Plugin

## Version

Check `metadata.txt` for the current plugin version.

