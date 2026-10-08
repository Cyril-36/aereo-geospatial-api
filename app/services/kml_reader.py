"""A small, hardened KML reader for a documented subset of KML 2.2.

Parsing uses defusedxml with DTDs, entities and external references forbidden. Nothing is
ever fetched: NetworkLinks and overlays are reported as warnings and skipped. Coordinates
are kept exactly as written (longitude, latitude[, altitude]); validation of the geometry
itself (ring closure, self-intersection) belongs to the measurement stage.
"""

import math
import re
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from app.errors import IngestionError
from app.services import crs
from app.services.dataset import (
    Dataset,
    FeatureBudget,
    IngestionLimits,
    RawFeature,
    json_safe,
    warning,
)

KML_NAMESPACES = {
    "http://www.opengis.net/kml/2.2",
    "http://earth.google.com/kml/2.0",
    "http://earth.google.com/kml/2.1",
    "http://earth.google.com/kml/2.2",
    "",  # no namespace
}
CONTAINERS = {"Document", "Folder"}
OVERLAYS = {"GroundOverlay", "ScreenOverlay", "PhotoOverlay"}
SIMPLE_GEOMETRIES = {"Point", "LineString", "LinearRing", "Polygon"}
UNSUPPORTED_GEOMETRIES = {"Track", "MultiTrack", "Model"}  # e.g. gx:Track
MAX_MULTIGEOMETRY_DEPTH = 32
_COMMA_SPACING = re.compile(r"\s*,\s*")


class _CoordinateError(ValueError):
    pass


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        ns, local = tag[1:].split("}", 1)
        return ns, local
    return "", tag


def read_kml(path: Path, limits: IngestionLimits) -> Dataset:
    data = path.read_bytes()
    if data.startswith(b"PK"):
        raise IngestionError(
            "KMZ_UNSUPPORTED",
            "This .kml file is a ZIP archive (probably a renamed KMZ). Upload the .kml inside it.",
        )
    try:
        root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise IngestionError(
            "UNSAFE_XML", "KML containing DTDs, entities or external references is rejected."
        ) from exc
    except (ParseError, ValueError) as exc:
        raise IngestionError("INVALID_KML", "The file is not well-formed XML.") from exc

    ns, local = _split_tag(root.tag)
    if local != "kml" or ns not in KML_NAMESPACES:
        raise IngestionError(
            "INVALID_KML", f"Root element must be <kml> in a KML namespace; found {root.tag!r}."
        )
    return _Reader(ns, limits).read(root)


class _Reader:
    def __init__(self, ns: str, limits: IngestionLimits) -> None:
        self.ns = ns
        self.budget = FeatureBudget(limits)
        self.features: list[RawFeature] = []
        self.skipped: dict[str, int] = {}

    def _local(self, element: Element) -> str | None:
        """Local name if the element is in the document's KML namespace, else None."""
        ns, local = _split_tag(element.tag) if isinstance(element.tag, str) else ("?", "")
        return local if ns == self.ns else None

    def _child(self, element: Element, name: str) -> Element | None:
        return next((c for c in element if self._local(c) == name), None)

    def _children(self, element: Element, name: str) -> list[Element]:
        return [c for c in element if self._local(c) == name]

    def read(self, root: Element) -> Dataset:
        # Iterative depth-first walk in document order: deep folder nesting cannot exhaust
        # the Python stack.
        stack: list[tuple[Element, tuple[str, ...]]] = [(root, ())]
        while stack:
            element, folders = stack.pop()
            if self._local(element) == "Placemark":
                self._placemark(element, list(folders))
                continue
            children = []
            for child in element:
                local = self._local(child)
                if local in CONTAINERS:
                    name_el = self._child(child, "name")
                    name = (name_el.text or "") if name_el is not None else ""
                    # Unnamed containers add no level to the reported folder path.
                    children.append((child, folders + (name,) if name else folders))
                elif local == "Placemark":
                    children.append((child, folders))
                elif local == "NetworkLink" or local in OVERLAYS:
                    self.skipped[local] = self.skipped.get(local, 0) + 1
            stack.extend(reversed(children))

        warnings = [
            warning(
                "NETWORK_LINK_IGNORED" if kind == "NetworkLink" else "OVERLAY_IGNORED",
                f"{count} {kind} element(s) skipped; remote content is never fetched.",
            )
            for kind, count in sorted(self.skipped.items())
        ]
        return Dataset("KML", crs.kml_crs(), self.features, warnings)

    def _placemark(self, placemark: Element, folders: list[str]) -> None:
        self.budget.add_feature()
        feature = RawFeature(
            index=len(self.features),
            source_id=placemark.get("id"),
            geometry_type=None,
            geometry=None,
            properties={},
            folder_path=folders,
        )
        self.features.append(feature)
        self._properties(placemark, feature)

        geometry_el = None
        for child in placemark:
            ns, local = _split_tag(child.tag) if isinstance(child.tag, str) else ("?", "")
            if ns == self.ns and (local in SIMPLE_GEOMETRIES or local == "MultiGeometry"):
                geometry_el = child
                break
            if local in UNSUPPORTED_GEOMETRIES:
                feature.geometry_type = local
                feature.issue_code = "UNSUPPORTED_GEOMETRY"
                feature.issue_detail = f"KML <{local}> is outside the supported subset."
                return
        if geometry_el is None:
            return  # no geometry: kept as a feature with null geometry

        try:
            geometry = self._geometry(geometry_el, depth=0)
        except _CoordinateError as exc:
            feature.geometry_type = self._local(geometry_el)
            feature.issue_code = "INVALID_COORDINATES"
            feature.issue_detail = str(exc)
            return
        if geometry is None:
            feature.geometry_type = self._local(geometry_el)
            return
        if geometry.get("unsupported"):
            feature.geometry_type = geometry["unsupported"]
            feature.issue_code = "UNSUPPORTED_GEOMETRY"
            feature.issue_detail = (
                f"KML <{geometry['unsupported']}> is outside the supported subset."
            )
            return
        feature.geometry_type = geometry["type"]
        feature.geometry = geometry

    def _properties(self, placemark: Element, feature: RawFeature) -> None:
        props = feature.properties

        def put(key: str, value: Any) -> None:
            final = key
            n = 2
            while final in props:
                final = f"{key}__{n}"
                n += 1
            if final != key:
                feature.warnings.append(
                    warning(
                        "PROPERTY_KEY_COLLISION", f"Property {key!r} repeated; stored as {final!r}."
                    )
                )
            props[final] = json_safe(value)

        for field in ("name", "description"):
            el = self._child(placemark, field)
            if el is not None:
                put(field, el.text or "")

        extended = self._child(placemark, "ExtendedData")
        if extended is None:
            return
        for data in self._children(extended, "Data"):
            value_el = self._child(data, "value")
            put(data.get("name") or "_unnamed", None if value_el is None else (value_el.text or ""))
        for schema_data in self._children(extended, "SchemaData"):
            for simple in self._children(schema_data, "SimpleData"):
                put(simple.get("name") or "_unnamed", simple.text or "")

    def _coordinates(self, element: Element) -> list[list[float]]:
        coords_el = self._child(element, "coordinates")
        text = (coords_el.text or "") if coords_el is not None else ""
        positions = []
        for token in _COMMA_SPACING.sub(",", text).split():
            parts = token.split(",")
            if len(parts) not in (2, 3):
                raise _CoordinateError(f"Coordinate tuple {token!r} must be lon,lat[,alt].")
            try:
                values = [float(p) for p in parts]
            except ValueError as exc:
                raise _CoordinateError(f"Coordinate tuple {token!r} is not numeric.") from exc
            if not all(math.isfinite(v) for v in values):
                raise _CoordinateError(f"Coordinate tuple {token!r} is not finite.")
            positions.append(values)
        self.budget.add_vertices(len(positions))
        return positions

    def _geometry(self, element: Element, depth: int) -> dict[str, Any] | None:
        local = self._local(element)
        if local == "Point":
            positions = self._coordinates(element)
            if not positions:
                return None
            if len(positions) != 1:
                raise _CoordinateError(f"Point has {len(positions)} coordinates; expected 1.")
            return {"type": "Point", "coordinates": positions[0]}
        if local in ("LineString", "LinearRing"):
            # A LinearRing outside a Polygon is a closed line; it is measured as a line.
            positions = self._coordinates(element)
            return {"type": "LineString", "coordinates": positions} if positions else None
        if local == "Polygon":
            return self._polygon(element)
        if local == "MultiGeometry":
            return self._multi(element, depth)
        return None

    def _polygon(self, element: Element) -> dict[str, Any] | None:
        outer = self._child(element, "outerBoundaryIs")
        outer_ring = self._child(outer, "LinearRing") if outer is not None else None
        if outer_ring is None:
            raise _CoordinateError("Polygon has no outerBoundaryIs/LinearRing.")
        rings = [self._coordinates(outer_ring)]
        if not rings[0]:
            return None
        # KML 2.2 puts one ring per innerBoundaryIs, but files with several rings inside
        # one innerBoundaryIs are common; accept both.
        for inner in self._children(element, "innerBoundaryIs"):
            for ring in self._children(inner, "LinearRing"):
                rings.append(self._coordinates(ring))
        return {"type": "Polygon", "coordinates": rings}

    def _multi(self, element: Element, depth: int) -> dict[str, Any] | None:
        if depth >= MAX_MULTIGEOMETRY_DEPTH:
            raise _CoordinateError("MultiGeometry nesting is too deep.")
        parts: list[dict[str, Any]] = []
        for child in element:
            ns, local = _split_tag(child.tag) if isinstance(child.tag, str) else ("?", "")
            if local in UNSUPPORTED_GEOMETRIES:
                return {"unsupported": local}
            if ns != self.ns:
                continue
            part = self._geometry(child, depth + 1)
            if part is None:
                continue
            if part.get("unsupported"):
                return part
            parts.extend(_flatten(part))
        if not parts:
            return None
        kinds = {p["type"] for p in parts}
        if kinds == {"Point"}:
            return {"type": "MultiPoint", "coordinates": [p["coordinates"] for p in parts]}
        if kinds == {"LineString"}:
            return {"type": "MultiLineString", "coordinates": [p["coordinates"] for p in parts]}
        if kinds == {"Polygon"}:
            return {"type": "MultiPolygon", "coordinates": [p["coordinates"] for p in parts]}
        return {"type": "GeometryCollection", "geometries": parts}


def _flatten(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    kind = geometry["type"]
    if kind == "GeometryCollection":
        return [g for part in geometry["geometries"] for g in _flatten(part)]
    if kind.startswith("Multi"):
        single = kind.removeprefix("Multi")
        return [{"type": single, "coordinates": c} for c in geometry["coordinates"]]
    return [geometry]
