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
DUSAF_QUERY_FORMAT = "geojson"


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
        self.page_size = int(page_size or self.default_page_size)

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
            "outFields": ",".join(out_fields) if out_fields else "*",
            "returnGeometry": "true",
            "resultRecordCount": self.page_size,
            "resultOffset": int(offset),
            "outSR": "32632",
        }

        if geometry is not None:
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
