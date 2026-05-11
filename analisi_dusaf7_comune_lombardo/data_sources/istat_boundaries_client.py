# -*- coding: utf-8 -*-

"""Client skeleton for official ISTAT municipal boundary datasets.

The module contains static source information and safe helpers only. It does
not download files, parse remote pages, or touch the QGIS project when imported.
"""

import os
import shutil
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


ISTAT_BOUNDARIES_PAGE_URL = (
    "https://www.istat.it/notizia/"
    "confini-delle-unita-amministrative-a-fini-statistici-al-1-gennaio-2018-2/"
)
ISTAT_REFERENCE_YEAR = 2026
ISTAT_DATASET_TYPE = "non_generalizzato"
ISTAT_EXPECTED_CRS_LABEL = "WGS84 UTM32N"
ISTAT_EXPECTED_LAYER_NAME = "Com01012026_WGS84"
ISTAT_EXPECTED_CRS_AUTHID = "EPSG:32632"
ISTAT_BOUNDARIES_2026_ZIP_URL = None
ISTAT_DOWNLOAD_TIMEOUT_SECONDS = 60
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


def validate_archive_destination_path(destination_path):
    """Validate and return a local destination path for an ISTAT ZIP archive.

    The function does not create files or directories. It rejects empty paths,
    directory-like paths, reserved filename segments, and non-ZIP destinations.
    Absolute paths are allowed because callers may place downloads inside the
    QGIS profile cache or another explicit workspace.
    """
    if not isinstance(destination_path, str) or not destination_path.strip():
        raise ValueError("ISTAT archive destination_path must be a non-empty string.")

    raw_path = destination_path.strip()
    path_parts = [part for part in raw_path.replace("\\", "/").split("/") if part]
    if ".." in path_parts:
        raise ValueError("ISTAT archive destination_path must not contain '..' segments.")

    path = os.path.abspath(raw_path)
    filename = os.path.basename(path)

    if filename in ("", ".", ".."):
        raise ValueError("ISTAT archive destination_path must include a valid filename.")

    if not filename.lower().endswith(".zip"):
        raise ValueError("ISTAT archive destination_path must point to a .zip file.")

    if os.path.isdir(path):
        raise ValueError("ISTAT archive destination_path points to a directory, not a file.")

    parent_dir = os.path.dirname(path)
    if parent_dir and not os.path.isdir(parent_dir):
        raise ValueError(
            "ISTAT archive destination directory does not exist: {}.".format(parent_dir)
        )

    return path


def validate_download_url(download_url):
    """Validate and return a configured ISTAT archive HTTPS URL."""
    if not isinstance(download_url, str) or not download_url.strip():
        raise ValueError(
            "ISTAT 2026 ZIP URL is not configured. Set ISTAT_BOUNDARIES_2026_ZIP_URL "
            "after verifying the official direct URL."
        )

    value = download_url.strip()

    if not value.startswith("https://"):
        raise ValueError("ISTAT 2026 ZIP URL must be an HTTPS URL.")

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

    if spec.dataset_type != ISTAT_DATASET_TYPE:
        errors.append("ISTAT dataset_type must be '{}'.".format(ISTAT_DATASET_TYPE))

    if spec.expected_crs_label != ISTAT_EXPECTED_CRS_LABEL:
        errors.append("ISTAT expected_crs_label must be '{}'.".format(ISTAT_EXPECTED_CRS_LABEL))

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
    dataset_type: str
    expected_crs_label: str
    expected_layer_name: str
    expected_crs_authid: str
    municipality_field_candidates: tuple
    download_url: str = None


class IstatBoundariesClient:
    """Prepare future access to ISTAT municipal boundaries.

    A later implementation should resolve the official zip URL explicitly,
    download it from a QgsTask, then validate the extracted layer before it is
    used by the Processing algorithm.
    """

    landing_page_url = ISTAT_BOUNDARIES_PAGE_URL
    reference_year = ISTAT_REFERENCE_YEAR
    dataset_type = ISTAT_DATASET_TYPE
    expected_crs_label = ISTAT_EXPECTED_CRS_LABEL
    expected_layer_name = ISTAT_EXPECTED_LAYER_NAME
    expected_crs_authid = ISTAT_EXPECTED_CRS_AUTHID
    download_url = ISTAT_BOUNDARIES_2026_ZIP_URL
    download_timeout_seconds = ISTAT_DOWNLOAD_TIMEOUT_SECONDS
    region_code_field = ISTAT_REGION_CODE_FIELD
    lombardia_region_code = ISTAT_REGION_CODE_LOMBARDIA
    municipality_field_candidates = ISTAT_MUNICIPALITY_FIELD_CANDIDATES

    def dataset_spec(self):
        """Return static metadata for the intended ISTAT dataset."""
        return IstatDatasetSpec(
            reference_year=self.reference_year,
            landing_page_url=self.landing_page_url,
            dataset_type=self.dataset_type,
            expected_crs_label=self.expected_crs_label,
            expected_layer_name=self.expected_layer_name,
            expected_crs_authid=self.expected_crs_authid,
            municipality_field_candidates=self.municipality_field_candidates,
            download_url=self.download_url,
        )

    def expected_archive_name_hint(self):
        """Return a conservative filename hint for future cache entries."""
        return f"confini_amministrativi_istat_{self.reference_year}.zip"

    def resolve_download_url(self):
        """Return the configured official ISTAT ZIP URL.

        No scraping is attempted here. If ISTAT changes the page or if the
        direct URL has not been verified yet, callers receive a clear error
        instead of relying on brittle HTML parsing.
        """
        return validate_download_url(self.download_url)

    def download_archive(self, destination_path, overwrite=False):
        """Download the configured ISTAT ZIP archive when explicitly called.

        The method does not extract the archive and does not convert shapefiles.
        It writes only to the validated destination path, refuses to overwrite
        existing files by default, and raises clear exceptions for missing URLs,
        HTTP failures, network failures, and filesystem write errors.
        """
        destination_path = validate_archive_destination_path(destination_path)
        download_url = self.resolve_download_url()

        if os.path.exists(destination_path) and not overwrite:
            raise FileExistsError(
                "ISTAT archive already exists and overwrite=False: {}.".format(destination_path)
            )

        temp_path = destination_path + ".download"
        if os.path.exists(temp_path):
            raise FileExistsError(
                "Temporary ISTAT download file already exists: {}.".format(temp_path)
            )

        try:
            with urlopen(download_url, timeout=self.download_timeout_seconds) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise ValueError(
                        "ISTAT archive download failed with HTTP status {}.".format(status)
                    )

                with open(temp_path, "wb") as handle:
                    shutil.copyfileobj(response, handle)

            os.replace(temp_path, destination_path)
            return destination_path

        except HTTPError as exc:
            raise ValueError(
                "ISTAT archive download failed with HTTP status {}.".format(exc.code)
            ) from exc
        except URLError as exc:
            raise ValueError("ISTAT archive network error: {}.".format(exc.reason)) from exc
        except TimeoutError as exc:
            raise ValueError("ISTAT archive download timed out.") from exc
        except OSError:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise
