# -*- coding: utf-8 -*-

"""Client skeleton for official ISTAT municipal boundary datasets.

The module contains static source information and safe helpers only. It does
not download files, parse remote pages, or touch the QGIS project when imported.
"""

from dataclasses import dataclass


ISTAT_BOUNDARIES_PAGE_URL = (
    "https://www.istat.it/notizia/"
    "confini-delle-unita-amministrative-a-fini-statistici-al-1-gennaio-2018-2/"
)
ISTAT_REFERENCE_YEAR = 2026
ISTAT_EXPECTED_LAYER_NAME = "Com01012026_WGS84"
ISTAT_EXPECTED_CRS_AUTHID = "EPSG:32632"
ISTAT_REGION_CODE_FIELD = "COD_REG"
ISTAT_REGION_CODE_LOMBARDIA = 3
ISTAT_MUNICIPALITY_FIELD_CANDIDATES = (
    "COMUNE",
    "DEN_COM",
    "DENOM_COM",
    "DENOMINAZ",
    "DENOMINAZI",
    "NOME_COM",
    "NOME",
)


def validate_reference_year(reference_year):
    """Validate and return an ISTAT administrative boundary reference year."""
    if isinstance(reference_year, bool):
        raise ValueError("ISTAT reference_year must be an integer, not a boolean value.")

    try:
        value = int(reference_year)
    except (TypeError, ValueError):
        raise ValueError("ISTAT reference_year must be an integer.") from None

    if value < 2002:
        raise ValueError("ISTAT reference_year must be 2002 or later for this dataset family.")

    return value


def validate_expected_layer_name(layer_name):
    """Validate and return the expected ISTAT layer name.

    Only a simple layer/file stem is accepted. Paths are intentionally rejected
    so future cache code cannot accidentally use a layer name as a filesystem
    path.
    """
    if not isinstance(layer_name, str) or not layer_name.strip():
        raise ValueError("ISTAT expected layer name must be a non-empty string.")

    value = layer_name.strip()

    if "/" in value or "\\" in value:
        raise ValueError("ISTAT expected layer name must not contain path separators.")

    if value in (".", ".."):
        raise ValueError("ISTAT expected layer name must not be a reserved path segment.")

    return value


def validate_dataset_spec(spec):
    """Validate an ISTAT dataset spec already present in memory.

    The function performs no network or file access. It returns a list of
    technical validation errors; an empty list means the structure is suitable
    for future use.
    """
    errors = []

    if not isinstance(spec, IstatDatasetSpec):
        return ["ISTAT dataset spec must be an IstatDatasetSpec instance."]

    try:
        validate_reference_year(spec.reference_year)
    except ValueError as exc:
        errors.append(str(exc))

    try:
        validate_expected_layer_name(spec.expected_layer_name)
    except ValueError as exc:
        errors.append(str(exc))

    if not isinstance(spec.landing_page_url, str) or not spec.landing_page_url.startswith("https://"):
        errors.append("ISTAT landing_page_url must be an HTTPS URL string.")

    if not isinstance(spec.expected_crs_authid, str) or not spec.expected_crs_authid.startswith("EPSG:"):
        errors.append("ISTAT expected_crs_authid must be an EPSG authid string.")

    if not spec.municipality_field_candidates:
        errors.append("ISTAT municipality_field_candidates must not be empty.")

    return errors


@dataclass(frozen=True)
class IstatDatasetSpec:
    """Description of an ISTAT boundary dataset expected by the plugin."""

    reference_year: int
    landing_page_url: str
    expected_layer_name: str
    expected_crs_authid: str
    municipality_field_candidates: tuple


class IstatBoundariesClient:
    """Prepare future access to ISTAT municipal boundaries.

    A later implementation should resolve the official zip URL explicitly,
    download it from a QgsTask, then validate the extracted layer before it is
    used by the Processing algorithm.
    """

    landing_page_url = ISTAT_BOUNDARIES_PAGE_URL
    reference_year = ISTAT_REFERENCE_YEAR
    expected_layer_name = ISTAT_EXPECTED_LAYER_NAME
    expected_crs_authid = ISTAT_EXPECTED_CRS_AUTHID
    region_code_field = ISTAT_REGION_CODE_FIELD
    lombardia_region_code = ISTAT_REGION_CODE_LOMBARDIA
    municipality_field_candidates = ISTAT_MUNICIPALITY_FIELD_CANDIDATES

    def dataset_spec(self):
        """Return static metadata for the intended ISTAT dataset."""
        return IstatDatasetSpec(
            reference_year=self.reference_year,
            landing_page_url=self.landing_page_url,
            expected_layer_name=self.expected_layer_name,
            expected_crs_authid=self.expected_crs_authid,
            municipality_field_candidates=self.municipality_field_candidates,
        )

    def expected_archive_name_hint(self):
        """Return a conservative filename hint for future cache entries."""
        return f"confini_amministrativi_istat_{self.reference_year}.zip"

    def resolve_download_url(self):
        """Placeholder for future official ISTAT zip URL resolution."""
        raise NotImplementedError(
            "ISTAT boundary download URL resolution is not implemented yet. "
            "This stub performs no network access."
        )

    def fetch_archive(self, *args, **kwargs):
        """Placeholder for future asynchronous archive retrieval."""
        raise NotImplementedError(
            "ISTAT boundary download is not implemented yet. This stub is "
            "intentionally not connected to the Processing workflow."
        )
