# -*- coding: utf-8 -*-

"""Client skeleton for Regione Lombardia DUSAF 7 ArcGIS REST data.

The module only stores endpoint metadata and conservative helper methods. It
does not perform network activity at import time and it is not integrated into
the current Processing workflow.
"""

from dataclasses import dataclass
from urllib.parse import urlencode


DUSAF_SERVICE_URL = (
    "https://www.cartografia.servizirl.it/arcgis1/rest/services/"
    "territorio/dusaf7/MapServer"
)
DUSAF_LAYER_ID = 1
DUSAF_LAYER_URL = f"{DUSAF_SERVICE_URL}/{DUSAF_LAYER_ID}"
DUSAF_EXPECTED_CRS_AUTHID = "EPSG:32632"
DUSAF_CLASS_FIELD = "COD_TOT"
DUSAF_DESCRIPTION_FIELD = "DESCR"
DUSAF_DEFAULT_PAGE_SIZE = 1000
DUSAF_MIN_PAGE_SIZE = 1
DUSAF_MAX_PAGE_SIZE = 1000
DUSAF_QUERY_FORMAT = "geojson"
DUSAF_ENVELOPE_KEYS = ("xmin", "ymin", "xmax", "ymax")


def _coerce_number(value, label):
    """Return a finite numeric value for validation-only helpers."""
    if isinstance(value, bool):
        raise ValueError(f"DUSAF {label} must be numeric, not a boolean value.")

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"DUSAF {label} must be numeric.") from None

    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"DUSAF {label} must be a finite numeric value.")

    return number


def validate_page_size(page_size):
    """Validate and return an ArcGIS REST page size.

    Regione Lombardia's service advertises a conservative record limit of 1000.
    This helper keeps future callers inside that range and performs no network
    request.
    """
    if isinstance(page_size, bool):
        raise ValueError("DUSAF page_size must be an integer, not a boolean value.")

    try:
        value = int(page_size)
    except (TypeError, ValueError):
        raise ValueError("DUSAF page_size must be an integer.") from None

    if value < DUSAF_MIN_PAGE_SIZE or value > DUSAF_MAX_PAGE_SIZE:
        raise ValueError(
            "DUSAF page_size must be between {} and {} records.".format(
                DUSAF_MIN_PAGE_SIZE,
                DUSAF_MAX_PAGE_SIZE,
            )
        )

    return value


def validate_offset(offset):
    """Validate and return a zero-based ArcGIS REST pagination offset."""
    if isinstance(offset, bool):
        raise ValueError("DUSAF offset must be an integer, not a boolean value.")

    try:
        value = int(offset)
    except (TypeError, ValueError):
        raise ValueError("DUSAF offset must be an integer.") from None

    if value < 0:
        raise ValueError("DUSAF offset must be greater than or equal to zero.")

    return value


def validate_envelope_32632(envelope):
    """Validate and return an ArcGIS envelope in EPSG:32632.

    The accepted inputs are a dict with ``xmin``, ``ymin``, ``xmax`` and
    ``ymax`` keys, or a four-item sequence in the same order. The function does
    not check Lombardia bounds; it only ensures a sane projected envelope.
    """
    if isinstance(envelope, dict):
        missing = [key for key in DUSAF_ENVELOPE_KEYS if key not in envelope]
        if missing:
            raise ValueError("DUSAF envelope is missing keys: {}.".format(", ".join(missing)))
        values = [envelope[key] for key in DUSAF_ENVELOPE_KEYS]
    else:
        try:
            values = list(envelope)
        except TypeError:
            raise ValueError("DUSAF envelope must be a dict or a four-item sequence.") from None

        if len(values) != 4:
            raise ValueError("DUSAF envelope sequence must contain exactly four values.")

    xmin, ymin, xmax, ymax = [
        _coerce_number(value, key) for value, key in zip(values, DUSAF_ENVELOPE_KEYS)
    ]

    if xmin >= xmax:
        raise ValueError("DUSAF envelope xmin must be smaller than xmax.")

    if ymin >= ymax:
        raise ValueError("DUSAF envelope ymin must be smaller than ymax.")

    return {
        "xmin": xmin,
        "ymin": ymin,
        "xmax": xmax,
        "ymax": ymax,
        "spatialReference": {"wkid": 32632},
    }


def validate_out_fields(out_fields):
    """Validate and return ArcGIS outFields as a comma-separated string."""
    if out_fields is None:
        return "*"

    if isinstance(out_fields, str):
        fields = [field.strip() for field in out_fields.split(",")]
    else:
        try:
            fields = []
            for field in out_fields:
                if isinstance(field, bool):
                    raise ValueError("DUSAF out_fields values must be strings, not booleans.")
                fields.append(str(field).strip())
        except TypeError:
            raise ValueError("DUSAF out_fields must be None, a string, or an iterable.") from None

    fields = [field for field in fields if field]

    if not fields:
        raise ValueError("DUSAF out_fields must not be empty.")

    if "*" in fields:
        if len(fields) != 1:
            raise ValueError("DUSAF out_fields '*' cannot be combined with named fields.")
        return "*"

    for field in fields:
        if not field.replace("_", "").isalnum():
            raise ValueError(f"Unsafe DUSAF out_field name: {field}.")

    return ",".join(fields)


def validate_arcgis_json_response(response_json):
    """Validate an ArcGIS JSON/GeoJSON response already loaded in memory."""
    if not isinstance(response_json, dict):
        raise ValueError("DUSAF ArcGIS response must be a dictionary.")

    if "error" in response_json:
        error = response_json["error"]
        if isinstance(error, dict):
            message = error.get("message") or error.get("details") or error
        else:
            message = error
        raise ValueError(f"DUSAF ArcGIS response contains an error: {message}.")

    if "features" in response_json and not isinstance(response_json["features"], list):
        raise ValueError("DUSAF ArcGIS response field 'features' must be a list.")

    return response_json


def response_has_features(response_json):
    """Return True when a validated ArcGIS response contains at least one feature."""
    response_json = validate_arcgis_json_response(response_json)
    return bool(response_json.get("features"))


def response_exceeded_transfer_limit(response_json):
    """Return True when ArcGIS reports that the transfer limit was exceeded."""
    response_json = validate_arcgis_json_response(response_json)
    return bool(response_json.get("exceededTransferLimit"))


def next_offset(response_json, current_offset, page_size):
    """Return the next prudent pagination offset, or None when paging can stop."""
    response_json = validate_arcgis_json_response(response_json)
    current_offset = validate_offset(current_offset)
    page_size = validate_page_size(page_size)
    feature_count = len(response_json.get("features") or [])

    if feature_count == 0:
        return None

    if response_exceeded_transfer_limit(response_json) or feature_count >= page_size:
        return current_offset + feature_count

    return None


@dataclass(frozen=True)
class ArcGisQuerySpec:
    """Description of an ArcGIS REST query that may be executed later."""

    url: str
    params: dict

    def as_url(self):
        """Return the complete URL without executing the request."""
        return f"{self.url}?{urlencode(self.params)}"


class LombardiaDusafClient:
    """Prepare future DUSAF 7 ArcGIS REST requests.

    This class deliberately avoids any direct download implementation for now.
    A later integration can use QGIS/Qt network APIs from a QgsTask so QGIS UI
    remains responsive.
    """

    service_url = DUSAF_SERVICE_URL
    layer_id = DUSAF_LAYER_ID
    layer_url = DUSAF_LAYER_URL
    expected_crs_authid = DUSAF_EXPECTED_CRS_AUTHID
    class_field = DUSAF_CLASS_FIELD
    description_field = DUSAF_DESCRIPTION_FIELD
    default_page_size = DUSAF_DEFAULT_PAGE_SIZE

    def __init__(self, page_size=None):
        if page_size is None:
            page_size = self.default_page_size

        self.page_size = validate_page_size(page_size)

    def metadata(self):
        """Return static metadata expected by the current algorithm."""
        return {
            "service_url": self.service_url,
            "layer_url": self.layer_url,
            "layer_id": self.layer_id,
            "expected_crs_authid": self.expected_crs_authid,
            "required_fields": [self.class_field, self.description_field],
            "page_size": self.page_size,
        }

    def build_query_spec(self, geometry=None, out_fields=None, offset=0):
        """Build a paged ArcGIS REST query description without running it.

        Args:
            geometry: Optional ArcGIS REST geometry payload to be used by a
                future spatial query.
            out_fields: Iterable of fields to request. Defaults to all fields.
            offset: Zero-based pagination offset.

        Returns:
            ArcGisQuerySpec: URL and parameters ready for a future network
            layer to execute.
        """
        params = {
            "f": DUSAF_QUERY_FORMAT,
            "where": "1=1",
            "outFields": validate_out_fields(out_fields),
            "returnGeometry": "true",
            "resultRecordCount": self.page_size,
            "resultOffset": validate_offset(offset),
            "outSR": "32632",
        }

        if geometry is not None:
            geometry = validate_envelope_32632(geometry)
            params.update(
                {
                    "geometry": geometry,
                    "geometryType": "esriGeometryEnvelope",
                    "spatialRel": "esriSpatialRelIntersects",
                    "inSR": "32632",
                }
            )

        return ArcGisQuerySpec(url=f"{self.layer_url}/query", params=params)

    def fetch_features(self, *args, **kwargs):
        """Placeholder for future asynchronous feature retrieval."""
        raise NotImplementedError(
            "DUSAF 7 download is not implemented yet. This stub is intentionally "
            "not connected to the Processing workflow."
        )
