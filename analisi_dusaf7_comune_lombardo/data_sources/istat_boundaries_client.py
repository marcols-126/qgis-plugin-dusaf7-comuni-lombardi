# -*- coding: utf-8 -*-

"""Client skeleton for official ISTAT municipal boundary datasets.

The module contains static source information and safe helpers only. It does
not download files, parse remote pages, or touch the QGIS project when imported.
"""

import os
import shutil
import zipfile
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .cache_manager import ISTAT_CACHE_FOLDER


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
ISTAT_REQUIRED_SHAPEFILE_EXTENSIONS = (".shp", ".dbf", ".shx", ".prj")
ISTAT_EXTRACTED_FOLDER_NAME = "extracted_2026"
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


def validate_existing_archive_path(archive_path):
    """Validate and return an existing local ISTAT ZIP archive path.

    The function performs no extraction. It verifies that the path exists,
    points to a regular ``.zip`` file, and can be opened as a ZIP archive.
    """
    if not isinstance(archive_path, str) or not archive_path.strip():
        raise ValueError("ISTAT archive_path must be a non-empty string.")

    path = os.path.abspath(archive_path.strip())

    if not os.path.exists(path):
        raise FileNotFoundError("ISTAT archive does not exist: {}.".format(path))

    if not os.path.isfile(path):
        raise ValueError("ISTAT archive path is not a file: {}.".format(path))

    if not path.lower().endswith(".zip"):
        raise ValueError("ISTAT archive path must point to a .zip file: {}.".format(path))

    if not zipfile.is_zipfile(path):
        raise ValueError("ISTAT archive is not a readable ZIP file: {}.".format(path))

    return path


def validate_extract_destination_dir(destination_dir):
    """Validate and return an existing destination directory for extraction."""
    if not isinstance(destination_dir, str) or not destination_dir.strip():
        raise ValueError("ISTAT extract destination_dir must be a non-empty string.")

    path = os.path.abspath(destination_dir.strip())

    if not os.path.isdir(path):
        raise ValueError("ISTAT extract destination_dir does not exist: {}.".format(path))

    return path


def validate_zip_member_name(member_name):
    """Validate one ZIP member name against path traversal risks."""
    if not isinstance(member_name, str) or not member_name.strip():
        raise ValueError("ISTAT ZIP contains an empty member name.")

    normalized = member_name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]

    if normalized.startswith("/") or os.path.isabs(member_name):
        raise ValueError("Unsafe ISTAT ZIP member uses an absolute path: {}.".format(member_name))

    if ".." in parts:
        raise ValueError("Unsafe ISTAT ZIP member contains '..': {}.".format(member_name))

    return normalized


def list_archive_files(archive_path):
    """Return validated non-directory file names contained in an ISTAT ZIP.

    Raises:
        ValueError: If the archive is invalid or contains unsafe member names.
    """
    archive_path = validate_existing_archive_path(archive_path)
    files = []

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            for info in archive.infolist():
                member_name = validate_zip_member_name(info.filename)
                if info.is_dir() or member_name.endswith("/"):
                    continue
                files.append(member_name)
    except zipfile.BadZipFile as exc:
        raise ValueError("ISTAT archive is corrupt or unreadable: {}.".format(archive_path)) from exc

    return files


def find_shapefile_components(archive_path, layer_name=ISTAT_EXPECTED_LAYER_NAME):
    """Inspect an ISTAT ZIP for expected shapefile component files.

    Returns a dictionary with ``present``, ``missing``, and ``files`` keys.
    Matching is case-insensitive and accepts components in nested safe folders.
    """
    layer_name = validate_expected_layer_name(layer_name)
    archive_files = list_archive_files(archive_path)
    expected = {
        extension: "{}{}".format(layer_name, extension).lower()
        for extension in ISTAT_REQUIRED_SHAPEFILE_EXTENSIONS
    }
    present = {}

    for archive_file in archive_files:
        basename = os.path.basename(archive_file).lower()
        for extension, expected_name in expected.items():
            if basename == expected_name:
                present[extension] = archive_file

    missing = [
        extension for extension in ISTAT_REQUIRED_SHAPEFILE_EXTENSIONS
        if extension not in present
    ]

    return {
        "present": present,
        "missing": missing,
        "files": archive_files,
    }


def validate_required_shapefile_components(archive_path, layer_name=ISTAT_EXPECTED_LAYER_NAME):
    """Raise a clear error if required shapefile components are missing."""
    components = find_shapefile_components(archive_path, layer_name=layer_name)

    if components["missing"]:
        raise ValueError(
            "ISTAT archive is missing required shapefile components for '{}': {}.".format(
                layer_name,
                ", ".join(components["missing"]),
            )
        )

    return components


def extract_archive(archive_path, destination_dir):
    """Extract an ISTAT ZIP into an existing directory after safety checks.

    The function blocks absolute paths and ``..`` traversal entries before
    extraction. It does not convert shapefiles or alter QGIS project state.
    """
    archive_path = validate_existing_archive_path(archive_path)
    destination_dir = validate_extract_destination_dir(destination_dir)
    extracted_paths = []

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = archive.infolist()
            for info in members:
                validate_zip_member_name(info.filename)

            destination_root = os.path.abspath(destination_dir)

            for info in members:
                member_name = validate_zip_member_name(info.filename)
                target_path = os.path.abspath(os.path.join(destination_root, member_name))

                if target_path != destination_root and not target_path.startswith(destination_root + os.sep):
                    raise ValueError(
                        "Unsafe ISTAT ZIP member would extract outside destination: {}.".format(
                            info.filename
                        )
                    )

                if info.is_dir():
                    os.makedirs(target_path, exist_ok=True)
                    continue

                os.makedirs(os.path.dirname(target_path), exist_ok=True)

                with archive.open(info, "r") as source, open(target_path, "wb") as target:
                    shutil.copyfileobj(source, target)

                extracted_paths.append(target_path)

    except zipfile.BadZipFile as exc:
        raise ValueError("ISTAT archive is corrupt or unreadable: {}.".format(archive_path)) from exc

    return extracted_paths


def validate_extracted_directory(extracted_dir):
    """Validate and return an existing directory containing extracted ISTAT files."""
    if not isinstance(extracted_dir, str) or not extracted_dir.strip():
        raise ValueError("ISTAT extracted_dir must be a non-empty string.")

    path = os.path.abspath(extracted_dir.strip())

    if not os.path.isdir(path):
        raise ValueError("ISTAT extracted_dir does not exist or is not a directory: {}.".format(path))

    return path


def find_extracted_shapefile(extracted_dir, layer_name=ISTAT_EXPECTED_LAYER_NAME):
    """Find the main ISTAT municipal shapefile in an extracted directory.

    The expected file ``Com01012026_WGS84.shp`` is preferred. If it is not
    found, the function looks recursively for a single ``.shp`` file whose
    basename matches the expected layer name case-insensitively.
    """
    extracted_dir = validate_extracted_directory(extracted_dir)
    layer_name = validate_expected_layer_name(layer_name)
    expected_filename = "{}.shp".format(layer_name)
    direct_path = os.path.join(extracted_dir, expected_filename)

    if os.path.isfile(direct_path):
        return os.path.abspath(direct_path)

    matches = []
    expected_lower = expected_filename.lower()

    for root, dirnames, filenames in os.walk(extracted_dir):
        dirnames[:] = [name for name in dirnames if name not in (".", "..")]
        for filename in filenames:
            if filename.lower() == expected_lower:
                matches.append(os.path.abspath(os.path.join(root, filename)))

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        raise ValueError(
            "Multiple ISTAT shapefiles named '{}' found in extracted directory: {}.".format(
                expected_filename,
                extracted_dir,
            )
        )

    raise FileNotFoundError(
        "Expected ISTAT shapefile '{}' was not found in extracted directory: {}.".format(
            expected_filename,
            extracted_dir,
        )
    )


def validate_extracted_shapefile_components(shp_path):
    """Validate required component files for an extracted ISTAT shapefile.

    The function checks only filesystem presence of ``.shp``, ``.dbf``, ``.shx``
    and ``.prj`` files sharing the same basename. It does not load the layer in
    QGIS and does not inspect attributes.
    """
    if not isinstance(shp_path, str) or not shp_path.strip():
        raise ValueError("ISTAT shapefile path must be a non-empty string.")

    path = os.path.abspath(shp_path.strip())

    if not os.path.isfile(path):
        raise FileNotFoundError("ISTAT shapefile does not exist: {}.".format(path))

    if not path.lower().endswith(".shp"):
        raise ValueError("ISTAT shapefile path must point to a .shp file: {}.".format(path))

    stem = os.path.splitext(path)[0]
    present = {}
    missing = []

    for extension in ISTAT_REQUIRED_SHAPEFILE_EXTENSIONS:
        component_path = stem + extension
        if os.path.isfile(component_path):
            present[extension] = component_path
        else:
            missing.append(extension)

    if missing:
        raise ValueError(
            "ISTAT shapefile is missing required component files: {}.".format(
                ", ".join(missing),
            )
        )

    return present


def resolve_valid_extracted_shapefile(extracted_dir, layer_name=ISTAT_EXPECTED_LAYER_NAME):
    """Return the full path to a valid extracted ISTAT ``.shp`` file."""
    shp_path = find_extracted_shapefile(extracted_dir, layer_name=layer_name)
    validate_extracted_shapefile_components(shp_path)
    return shp_path


def build_local_package_manifest_entry(
    archive_path,
    extract_dir,
    shapefile_path,
    component_paths,
    client,
):
    """Build manifest metadata for a prepared local ISTAT boundary package."""
    return {
        "source": client.landing_page_url,
        "reference_year": client.reference_year,
        "dataset_type": client.dataset_type,
        "expected_crs_label": client.expected_crs_label,
        "expected_crs_authid": client.expected_crs_authid,
        "expected_layer_name": client.expected_layer_name,
        "archive_path": os.path.abspath(archive_path),
        "extract_dir": os.path.abspath(extract_dir),
        "shapefile_path": os.path.abspath(shapefile_path),
        "components": {
            extension: os.path.abspath(path)
            for extension, path in sorted(component_paths.items())
        },
    }


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

    def list_archive_files(self, archive_path):
        """Return safe file names from a previously downloaded ISTAT ZIP."""
        return list_archive_files(archive_path)

    def find_shapefile_components(self, archive_path):
        """Inspect a ZIP for expected ISTAT shapefile components."""
        return find_shapefile_components(archive_path, self.expected_layer_name)

    def validate_required_shapefile_components(self, archive_path):
        """Validate that a ZIP contains .shp, .dbf, .shx and .prj files."""
        return validate_required_shapefile_components(archive_path, self.expected_layer_name)

    def extract_archive(self, archive_path, destination_dir):
        """Extract a previously downloaded ISTAT ZIP after safety checks."""
        return extract_archive(archive_path, destination_dir)

    def find_extracted_shapefile(self, extracted_dir):
        """Find the expected extracted ISTAT municipal .shp file."""
        return find_extracted_shapefile(extracted_dir, self.expected_layer_name)

    def validate_extracted_shapefile_components(self, shp_path):
        """Validate required sidecar files for an extracted ISTAT shapefile."""
        return validate_extracted_shapefile_components(shp_path)

    def resolve_valid_extracted_shapefile(self, extracted_dir):
        """Return a valid extracted ISTAT .shp path without loading it in QGIS."""
        return resolve_valid_extracted_shapefile(extracted_dir, self.expected_layer_name)

    def prepare_local_package(self, archive_path, cache_manager, overwrite=False):
        """Prepare and register a local ISTAT package from an existing ZIP.

        This method performs only local filesystem work when called explicitly:
        it validates the ZIP, creates the ISTAT cache directory, extracts the
        archive into a controlled subdirectory, validates the expected
        shapefile sidecar files, and updates the cache manifest. It does not
        download data, load QGIS layers, read attributes, or convert formats.
        """
        if cache_manager is None:
            raise ValueError("cache_manager is required to prepare a local ISTAT package.")

        archive_path = validate_existing_archive_path(archive_path)
        validate_required_shapefile_components(archive_path, self.expected_layer_name)

        dataset_dir = cache_manager.ensure_dataset_dir(ISTAT_CACHE_FOLDER)
        dataset_dir = os.path.abspath(dataset_dir)
        extract_dir = os.path.join(dataset_dir, ISTAT_EXTRACTED_FOLDER_NAME)

        if not extract_dir.startswith(dataset_dir + os.sep):
            raise ValueError(
                "ISTAT extraction directory is outside the dataset cache directory: {}.".format(
                    extract_dir
                )
            )

        if os.path.exists(extract_dir):
            if not overwrite:
                raise FileExistsError(
                    "ISTAT extraction directory already exists and overwrite=False: {}.".format(
                        extract_dir
                    )
                )
            if not os.path.isdir(extract_dir):
                raise ValueError(
                    "ISTAT extraction target exists but is not a directory: {}.".format(
                        extract_dir
                    )
                )
            shutil.rmtree(extract_dir)

        os.makedirs(extract_dir, exist_ok=True)
        extract_archive(archive_path, extract_dir)

        shapefile_path = resolve_valid_extracted_shapefile(
            extract_dir,
            self.expected_layer_name,
        )
        component_paths = validate_extracted_shapefile_components(shapefile_path)

        manifest = cache_manager.read_manifest()
        datasets = manifest.setdefault("datasets", {})
        datasets[ISTAT_CACHE_FOLDER] = build_local_package_manifest_entry(
            archive_path=archive_path,
            extract_dir=extract_dir,
            shapefile_path=shapefile_path,
            component_paths=component_paths,
            client=self,
        )
        cache_manager.write_manifest(manifest)

        return datasets[ISTAT_CACHE_FOLDER]
