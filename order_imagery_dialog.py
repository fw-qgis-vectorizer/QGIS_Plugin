# -*- coding: utf-8 -*-
"""
Order drone imagery dialog for FieldWatch VEC plugin.
Option 1: Embedded QgsMapCanvas with a minimal custom QgsMapTool (not QgsMapToolCapture)
to avoid native crash. User draws AOI on the map inside the dialog.
"""
import requests
from qgis.PyQt import QtWidgets, QtCore
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QFormLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QMessageBox,
    QStackedWidget, QWidget, QScrollArea, QFrame
)
from qgis.core import (
    QgsProject, QgsDistanceArea, QgsGeometry, QgsPointXY,
    QgsRectangle, QgsWkbTypes, QgsCoordinateReferenceSystem, QgsRasterLayer,
    QgsCoordinateTransform
)
from qgis.gui import QgsMapCanvas, QgsMapTool, QgsRubberBand

REQUEST_API_URL = "https://usefieldwatch.com/api/requests/public"


class SimplePolygonMapTool(QgsMapTool):
    """
    Minimal polygon capture tool for embedded canvas.
    Subclass of QgsMapTool only (not QgsMapToolCapture) to avoid
    advanced digitizing / CAD code that caused access violation.
    Left-click: add point. Right-click: finish polygon (emit geometry).
    """
    finished = QtCore.pyqtSignal(object)  # QgsGeometry

    def __init__(self, canvas):
        super(SimplePolygonMapTool, self).__init__(canvas)
        self.canvas = canvas
        self.points = []
        self.rubber_band = None

    def canvasReleaseEvent(self, event):
        if event.button() == Qt.RightButton:
            if len(self.points) >= 3:
                polygon_points = self.points + [self.points[0]]
                geometry = QgsGeometry.fromPolygonXY([polygon_points])
                self.finished.emit(geometry)
            else:
                self.finished.emit(QgsGeometry())
            self._cleanup_rubber_band()
            self.points = []
        elif event.button() == Qt.LeftButton:
            point = self.toMapCoordinates(event.pos())
            self.points.append(QgsPointXY(point))
            if self.rubber_band is None:
                self.rubber_band = QgsRubberBand(
                    self.canvas, QgsWkbTypes.PolygonGeometry
                )
                self.rubber_band.setColor(Qt.red)
                self.rubber_band.setWidth(2)
                # Semi-transparent fill so underlying imagery is visible
                self.rubber_band.setFillColor(QColor(255, 0, 0, 60))
            if len(self.points) > 1:
                self.rubber_band.reset(QgsWkbTypes.PolygonGeometry)
                for p in self.points:
                    self.rubber_band.addPoint(p, False)
                self.rubber_band.addPoint(self.points[0], True)
                self.rubber_band.show()

    def _cleanup_rubber_band(self):
        if self.rubber_band:
            self.rubber_band.reset()
            self.rubber_band = None

    def deactivate(self):
        self._cleanup_rubber_band()
        self.points = []
        super(SimplePolygonMapTool, self).deactivate()


def _geometry_area_hectares(geometry, crs):
    """Compute geometry area in hectares using ellipsoid."""
    if not geometry or geometry.isEmpty():
        return 0.0
    da = QgsDistanceArea()
    da.setSourceCrs(crs, QgsProject.instance().transformContext())
    da.setEllipsoid(crs.ellipsoidAcronym())
    area_m2 = da.measureArea(geometry)
    return area_m2 / 10000.0  # m² to hectares


def _polygon_to_lat_lng(geometry, source_crs):
    """Convert polygon geometry to list of {lat, lng} in WGS84 (EPSG:4326)."""
    if not geometry or geometry.isEmpty():
        return []
    dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance().transformContext())
    geom_4326 = QgsGeometry(geometry)
    geom_4326.transform(transform)
    coords = []
    # Single polygon: asPolygon(); multi-polygon: asMultiPolygon()
    poly_single = geom_4326.asPolygon()
    if poly_single:
        ring = poly_single[0]
    else:
        try:
            poly_multi = geom_4326.asMultiPolygon()
            if poly_multi:
                ring = poly_multi[0][0]
            else:
                return []
        except TypeError:
            return []
    for pt in ring:
        coords.append({"lat": pt.y(), "lng": pt.x()})
    # Remove duplicate closing vertex if present (polygon rings are closed in QGIS)
    if len(coords) > 1 and coords[0]["lat"] == coords[-1]["lat"] and coords[0]["lng"] == coords[-1]["lng"]:
        coords = coords[:-1]
    return coords


class OrderImageryDialog(QDialog):
    """Dialog for ordering drone imagery: AOI on embedded map, options, contact, submit."""

    def __init__(self, iface, parent=None):
        super(OrderImageryDialog, self).__init__(parent)
        self.iface = iface
        self.aoi_geometry = None
        self.canvas = None
        self.capture_tool = None
        self._basemap_layer = None  # keep reference so layer is not gc'd
        self.manual_points = []  # list of (lat, lng) tuples entered manually
        self._manual_points_rb = None  # rubber band for manual-points polygon
        self.setWindowTitle("Order Drone Imagery")
        self.setMinimumSize(520, 720)
        self._build_ui()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        self.stacked_widget = QStackedWidget()

        # --- Page 0: Order form ---
        form_page = QWidget()
        layout = QVBoxLayout(form_page)

        # --- Map / AOI (embedded canvas) ---
        map_group = QGroupBox("Area of interest (AOI)")
        map_layout = QVBoxLayout(map_group)

        btn_layout = QHBoxLayout()
        self.draw_aoi_btn = QPushButton("Draw AOI on map")
        self.draw_aoi_btn.clicked.connect(self._start_draw_aoi)
        self.clear_aoi_btn = QPushButton("Clear")
        self.clear_aoi_btn.setEnabled(False)
        self.clear_aoi_btn.clicked.connect(self._clear_aoi)
        btn_layout.addWidget(self.draw_aoi_btn)
        btn_layout.addWidget(self.clear_aoi_btn)
        map_layout.addLayout(btn_layout)

        self.area_label = QLabel("Area: — ha")
        self.area_label.setStyleSheet("font-weight: bold;")
        map_layout.addWidget(self.area_label)

        self.canvas = QgsMapCanvas()
        self.canvas.setMinimumHeight(280)
        self.canvas.setMaximumHeight(320)
        self._setup_canvas()
        map_layout.addWidget(self.canvas)

        info_label = QLabel(
            "Draw on the map above: left-click to add points, right-click to finish."
        )
        info_label.setWordWrap(True)
        map_layout.addWidget(info_label)

        layout.addWidget(map_group)

        # --- Optional: define AOI by coordinates (lat/lng) ---
        coords_group = QGroupBox("AOI by coordinates (lat/lng, WGS84)")
        coords_layout = QFormLayout(coords_group)

        # Single point entry, add up to 6 points
        self.point_lat_edit = QLineEdit()
        self.point_lng_edit = QLineEdit()
        self.point_lat_edit.setPlaceholderText("Lat")
        self.point_lng_edit.setPlaceholderText("Lng")
        point_row = QHBoxLayout()
        point_row.addWidget(self.point_lat_edit)
        point_row.addWidget(self.point_lng_edit)
        coords_layout.addRow("New point:", point_row)

        self.add_point_btn = QPushButton("Add point")
        self.add_point_btn.clicked.connect(self._add_point_from_fields)
        coords_layout.addRow(self.add_point_btn)

        self.points_label = QLabel("No points added yet.")
        self.points_label.setWordWrap(True)
        coords_layout.addRow("Current points:", self.points_label)

        self.use_coords_btn = QPushButton("Use points")
        self.use_coords_btn.clicked.connect(self._apply_coords_aoi)
        self.use_coords_btn.setEnabled(False)  # enabled when we have at least 3 points
        coords_layout.addRow(self.use_coords_btn)

        self.clear_points_btn = QPushButton("Clear points")
        self.clear_points_btn.clicked.connect(self._clear_manual_points)
        coords_layout.addRow(self.clear_points_btn)

        layout.addWidget(coords_group)

        # --- Resolution & Deliverable ---
        options_group = QGroupBox("Options")
        options_layout = QFormLayout(options_group)

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["1 cm GSD", "2 cm GSD", "5 cm GSD"])
        self.resolution_combo.currentIndexChanged.connect(self._update_price)
        options_layout.addRow("Resolution (GSD):", self.resolution_combo)

        self.deliverable_combo = QComboBox()
        self.deliverable_combo.addItems([
            "Orthomosaic",
            "DSM",
            "Point cloud"
        ])
        self.deliverable_combo.currentIndexChanged.connect(self._update_price)
        options_layout.addRow("Deliverable:", self.deliverable_combo)

        self.price_label = QLabel("Price estimate: —")
        self.price_label.setStyleSheet("font-weight: bold; color: #0a5f0a;")
        options_layout.addRow(self.price_label)

        layout.addWidget(options_group)

        # --- Contact ---
        contact_group = QGroupBox("Contact details")
        contact_layout = QFormLayout(contact_group)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Your name")
        contact_layout.addRow("Name:", self.name_edit)
        self.company_edit = QLineEdit()
        self.company_edit.setPlaceholderText("Company name")
        contact_layout.addRow("Company:", self.company_edit)
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("email@example.com")
        contact_layout.addRow("Email:", self.email_edit)
        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("Phone number")
        contact_layout.addRow("Phone:", self.phone_edit)
        layout.addWidget(contact_group)

        # --- Submit ---
        self.submit_btn = QPushButton("Submit request")
        self.submit_btn.setStyleSheet(
            "min-height: 28px; font-weight: bold;"
        )
        self.submit_btn.clicked.connect(self._submit_request)
        layout.addWidget(self.submit_btn)

        layout.addStretch(1)

        # Wrap form in scroll area so the dialog is scrollable on small screens
        form_scroll = QScrollArea()
        form_scroll.setWidget(form_page)
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.NoFrame)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        form_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.stacked_widget.addWidget(form_scroll)

        # --- Page 1: Confirmation (shown after successful submit) ---
        confirmation_page = QWidget()
        conf_layout = QVBoxLayout(confirmation_page)
        self.confirmation_title = QLabel("Request submitted successfully")
        self.confirmation_title.setStyleSheet("font-size: 14pt; font-weight: bold; color: #0a5f0a;")
        conf_layout.addWidget(self.confirmation_title)
        self.confirmation_text = QLabel()
        self.confirmation_text.setWordWrap(True)
        self.confirmation_text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        scroll = QScrollArea()
        scroll.setWidget(self.confirmation_text)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        conf_layout.addWidget(scroll)
        done_btn = QPushButton("Close")
        done_btn.clicked.connect(self.close)
        conf_layout.addWidget(done_btn)
        self.stacked_widget.addWidget(confirmation_page)

        main_layout.addWidget(self.stacked_widget)

    def _setup_canvas(self):
        """Set embedded canvas to a universal basemap (Esri World Imagery) and default extent."""
        # Esri World Imagery - satellite, no API key required
        uri = (
            "type=xyz&url=https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/%7Bz%7D/%7By%7D/%7Bx%7D"
            "&zmax=19&zmin=0&crs=EPSG3857"
        )
        self._basemap_layer = QgsRasterLayer(uri, "Esri World Imagery", "wms")
        if self._basemap_layer.isValid():
            self.canvas.setLayers([self._basemap_layer])
            crs = QgsCoordinateReferenceSystem("EPSG:3857")
            self.canvas.setDestinationCrs(crs)
            # Default extent: world in Web Mercator (meters)
            extent = QgsRectangle(-20037508, -20037508, 20037508, 20037508)
            self.canvas.setExtent(extent)
        else:
            # Fallback: empty canvas with world extent in 4326
            self.canvas.setLayers([])
            self.canvas.setExtent(QgsRectangle(-180, -90, 180, 90))
        self.canvas.refresh()

    def _start_draw_aoi(self):
        """Activate polygon capture on the embedded canvas (custom tool, no QgsMapToolCapture)."""
        if self.capture_tool is None:
            self.capture_tool = SimplePolygonMapTool(self.canvas)
            self.capture_tool.finished.connect(self._on_aoi_finished)
        self.canvas.setMapTool(self.capture_tool)
        self.draw_aoi_btn.setEnabled(False)
        self.area_label.setText("Drawing... Left-click to add points, right-click to finish.")
        self.area_label.setStyleSheet("font-weight: bold; color: #0066cc;")

    def _on_aoi_finished(self, geometry):
        """Handle AOI polygon completed on the embedded canvas."""
        self.canvas.unsetMapTool(self.capture_tool)
        self.draw_aoi_btn.setEnabled(True)

        if geometry and not geometry.isEmpty():
            self.aoi_geometry = geometry
            crs = self.canvas.mapSettings().destinationCrs()
            if not crs.isValid():
                crs = QgsProject.instance().crs()
            ha = _geometry_area_hectares(geometry, crs)
            self.area_label.setText("Area: {:.2f} ha".format(ha))
            self.area_label.setStyleSheet("font-weight: bold; color: green;")
            self.clear_aoi_btn.setEnabled(True)
            self._update_price()
        else:
            self.area_label.setText("Area: — ha (draw at least 3 points)")
            self.area_label.setStyleSheet("font-weight: bold; color: #666;")

    def _clear_aoi(self):
        """Clear AOI and reset area display."""
        self.aoi_geometry = None
        self.area_label.setText("Area: — ha")
        self.area_label.setStyleSheet("font-weight: bold;")
        self.clear_aoi_btn.setEnabled(False)
        self._update_price()
        if self.canvas:
            self.canvas.refresh()
        # Clear manual points and related UI
        self.manual_points = []
        if hasattr(self, "points_label"):
            self.points_label.setText("No points added yet.")
        if hasattr(self, "point_lat_edit"):
            self.point_lat_edit.clear()
        if hasattr(self, "point_lng_edit"):
            self.point_lng_edit.clear()
        if hasattr(self, "add_point_btn"):
            self.add_point_btn.setEnabled(True)
        if hasattr(self, "use_coords_btn"):
            self.use_coords_btn.setEnabled(False)
        if self._manual_points_rb is not None:
            self._manual_points_rb.reset()
            self._manual_points_rb = None

    def _update_price(self):
        """Update price estimate: $300 per km²."""
        if not self.aoi_geometry:
            self.price_label.setText("Price estimate: —")
            return
        crs = self.canvas.mapSettings().destinationCrs() if self.canvas else QgsProject.instance().crs()
        if not crs.isValid():
            crs = QgsProject.instance().crs()
        ha = _geometry_area_hectares(self.aoi_geometry, crs)
        area_km2 = ha / 100.0  # 1 km² = 100 ha
        price_per_km2 = 300  # USD
        estimate = area_km2 * price_per_km2
        self.price_label.setText("Price estimate: $ {:.0f} (${:.0f}/km²)".format(estimate, price_per_km2))

    def _add_point_from_fields(self):
        """Add a single lat/lng point (WGS84) to the manual points list."""
        try:
            lat = float(self.point_lat_edit.text().strip())
            lng = float(self.point_lng_edit.text().strip())
        except (TypeError, ValueError):
            QMessageBox.warning(
                self,
                "Invalid coordinates",
                "Please enter numeric latitude and longitude values."
            )
            return

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
            QMessageBox.warning(
                self,
                "Invalid coordinates",
                "Latitude must be between -90 and 90, longitude between -180 and 180."
            )
            return

        if len(self.manual_points) >= 6:
            QMessageBox.warning(
                self,
                "Too many points",
                "Maximum of 6 points allowed."
            )
            return

        self.manual_points.append((lat, lng))
        # Clear input fields
        self.point_lat_edit.clear()
        self.point_lng_edit.clear()

        # Update label with current points
        lines = []
        for i, (plat, plng) in enumerate(self.manual_points, 1):
            lines.append(f"{i}: ({plat:.6f}, {plng:.6f})")
        self.points_label.setText("\n".join(lines))

        # Enable use-points button when we have at least 3 points
        if len(self.manual_points) >= 3:
            self.use_coords_btn.setEnabled(True)
        # Disable add button when we hit 6 points
        if len(self.manual_points) >= 6 and hasattr(self, "add_point_btn"):
            self.add_point_btn.setEnabled(False)

    def _apply_coords_aoi(self):
        """Use manually added lat/lng points (WGS84) to define AOI and draw it on the Esri map."""
        if len(self.manual_points) < 3:
            QMessageBox.warning(
                self,
                "Not enough points",
                "Please add at least three points before using them as an AOI."
            )
            return

        # Build polygon in WGS84 from manual_points
        pts_wgs84 = [QgsPointXY(lng, lat) for (lat, lng) in self.manual_points]

        # Close ring and build polygon in WGS84
        ring_wgs84 = pts_wgs84 + [pts_wgs84[0]]
        geom_wgs84 = QgsGeometry.fromPolygonXY([ring_wgs84])

        # Transform to canvas CRS (Web Mercator)
        src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        dest_crs = self.canvas.mapSettings().destinationCrs() if self.canvas else QgsCoordinateReferenceSystem("EPSG:3857")
        if not dest_crs.isValid():
            dest_crs = QgsCoordinateReferenceSystem("EPSG:3857")
        xform = QgsCoordinateTransform(src_crs, dest_crs, QgsProject.instance().transformContext())
        geom_canvas = QgsGeometry(geom_wgs84)
        try:
            geom_canvas.transform(xform)
        except Exception as e:
            QMessageBox.warning(
                self,
                "Transform error",
                f"Could not transform coordinates to map CRS: {e}"
            )
            return

        self.aoi_geometry = geom_canvas

        # Draw polygon on embedded canvas (clear previous manual polygon if any)
        if self._manual_points_rb is None:
            self._manual_points_rb = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
            self._manual_points_rb.setColor(Qt.red)
            self._manual_points_rb.setWidth(2)
            # Semi-transparent fill so underlying imagery is visible
            self._manual_points_rb.setFillColor(QColor(255, 0, 0, 60))
        else:
            self._manual_points_rb.reset(QgsWkbTypes.PolygonGeometry)
        self._manual_points_rb.setToGeometry(geom_canvas, None)
        self.clear_aoi_btn.setEnabled(True)

        # Zoom to AOI
        self.canvas.setExtent(geom_canvas.boundingBox())
        self.canvas.refresh()

        # Update area & price
        self._update_price()

    def _clear_manual_points(self):
        """Clear only the manually added coordinate points and their AOI polygon."""
        self.manual_points = []
        if hasattr(self, "points_label"):
            self.points_label.setText("No points added yet.")
        if hasattr(self, "point_lat_edit"):
            self.point_lat_edit.clear()
        if hasattr(self, "point_lng_edit"):
            self.point_lng_edit.clear()
        if hasattr(self, "add_point_btn"):
            self.add_point_btn.setEnabled(True)
        if hasattr(self, "use_coords_btn"):
            self.use_coords_btn.setEnabled(False)
        if self._manual_points_rb is not None:
            self._manual_points_rb.reset()
            self._manual_points_rb = None
        # Do not touch self.aoi_geometry here; user may already have a drawn AOI from map

    def _submit_request(self):
        """Validate and submit the order to FieldWatch API."""
        if not self.aoi_geometry or self.aoi_geometry.isEmpty():
            QMessageBox.warning(
                self,
                "Area required",
                "Please draw an AOI on the map first."
            )
            return
        name = self.name_edit.text().strip()
        company = self.company_edit.text().strip()
        email = self.email_edit.text().strip()
        phone = self.phone_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing field", "Please enter your name.")
            return
        if not email:
            QMessageBox.warning(self, "Missing field", "Please enter your email.")
            return

        source_crs = self.canvas.mapSettings().destinationCrs()
        if not source_crs.isValid():
            source_crs = QgsProject.instance().crs()
        area_ha = _geometry_area_hectares(self.aoi_geometry, source_crs)
        polygon_coords = _polygon_to_lat_lng(self.aoi_geometry, source_crs)
        if not polygon_coords:
            QMessageBox.warning(
                self,
                "Error",
                "Could not convert polygon to coordinates."
            )
            return

        resolution = self.resolution_combo.currentText()
        deliverable = self.deliverable_combo.currentText()
        notes = "Resolution: {} | Deliverable: {}".format(resolution, deliverable)
        if phone:
            notes += " | Phone: {}".format(phone)

        payload = {
            "name": name,
            "email": email,
            "company": company or "",
            "serviceType": "drone_imagery",
            "notes": notes,
            "area": round(area_ha, 4),
            "polygonCoords": polygon_coords
        }

        try:
            response = requests.post(
                REQUEST_API_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=30
            )
            response.raise_for_status()
            self._show_confirmation(
                area_ha=area_ha,
                polygon_coords=polygon_coords,
                resolution=resolution,
                deliverable=deliverable,
                price_estimate=area_ha / 100.0 * 300,
                name=name,
                email=email,
                company=company or "",
                phone=phone
            )
        except requests.exceptions.Timeout:
            QMessageBox.warning(
                self,
                "Request failed",
                "The request timed out. Please check your connection and try again."
            )
        except requests.exceptions.RequestException as e:
            msg = str(e)
            if hasattr(e, "response") and e.response is not None:
                try:
                    body = e.response.text[:200] if e.response.text else ""
                    if body:
                        msg = "{} - {}".format(e.response.status_code, body)
                except Exception:
                    pass
            QMessageBox.warning(
                self,
                "Request failed",
                "Could not submit request: {}.".format(msg)
            )

    def _show_confirmation(self, area_ha, polygon_coords, resolution, deliverable,
                          price_estimate, name, email, company, phone):
        """Switch to confirmation tab and display the sent payload."""
        area_km2 = area_ha / 100.0
        lines = [
            "The following data was sent to FieldWatch:",
            "",
            "——— Contact ———",
            "Name: {}".format(name),
            "Email: {}".format(email),
            "Company: {}".format(company),
            "Phone: {}".format(phone) if phone else "Phone: —",
            "",
            "——— Area ———",
            "Area: {:.4f} ha ({:.4f} km²)".format(area_ha, area_km2),
            "",
            "——— Options ———",
            "Resolution: {}".format(resolution),
            "Deliverable: {}".format(deliverable),
            "",
            "——— Pricing ———",
            "Total: $ {:.0f} ($300/km²)".format(price_estimate),
            "",
            "——— Polygon coordinates (lat, lng) ———",
        ]
        for i, pt in enumerate(polygon_coords, 1):
            lines.append("  {}. lat: {:.6f}, lng: {:.6f}".format(i, pt["lat"], pt["lng"]))
        self.confirmation_text.setText("\n".join(lines))
        self.confirmation_text.setTextFormat(Qt.PlainText)
        self.stacked_widget.setCurrentIndex(1)

    def showEvent(self, event):
        """Refresh embedded canvas when dialog is shown."""
        super(OrderImageryDialog, self).showEvent(event)
        if self.canvas:
            self._setup_canvas()
