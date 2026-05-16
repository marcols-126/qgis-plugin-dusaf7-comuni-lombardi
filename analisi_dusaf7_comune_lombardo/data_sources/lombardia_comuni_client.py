# -*- coding: utf-8 -*-

"""Client for the Lombardia ArcGIS REST service that publishes the
"Ambiti Amministrativi Lombardia" feature layers (municipal boundaries).

Used as the *default* boundary source for the plugin: it is a REST service
maintained by Regione Lombardia, already in EPSG:32632, paginated to 1000
features per page, and aligned with the DUSAF service in terms of attributes
and CRS. ISTAT remains the optional authoritative override and is handled by
``istat_boundaries_client``.

This module performs no network access at import time and does not interact
with the QGIS project. Validators are pure and may be exercised in isolation
from the Python Console.
"""

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import urlopen


_ALLOWED_URL_SCHEMES = ("http", "https")


def _validate_url_scheme(url):
    """Reject URLs whose scheme is not HTTP(S).

    Bandit's B310 ("urllib_urlopen") warns that ``urlopen`` accepts any
    scheme, including ``file://`` and other schemes that can be abused as
    SSRF or local-file disclosure vectors. We validate the scheme up-front
    so the call site can safely use ``urlopen`` for HTTP(S) only.
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError(
            "Comuni ArcGIS URL has unsupported scheme '{}': only http/https allowed.".format(
                parsed.scheme
            )
        )
    return url


COMUNI_SERVICE_URL = (
    "https://www.cartografia.servizirl.it/arcgis/rest/services/"
    "trasversali/Ambiti_Amministrativi_Lombardia/MapServer"
)
COMUNI_LAYER_ID = 1
COMUNI_LAYER_URL = f"{COMUNI_SERVICE_URL}/{COMUNI_LAYER_ID}"

COMUNI_EXPECTED_CRS_AUTHID = "EPSG:32632"
COMUNI_EXPECTED_CRS_WKID = 32632

COMUNI_NAME_FIELD = "NOME_COM"
COMUNI_ISTAT_FIELD = "ISTAT"
COMUNI_ISTATN_FIELD = "COD_ISTATN"
COMUNI_BELFIORE_FIELD = "BELFIORE"
COMUNI_PROVINCE_CODE_FIELD = "COD_PRO"
COMUNI_PROVINCE_NAME_FIELD = "NOME_PRO"
COMUNI_PROVINCE_SHORT_FIELD = "SIG_PRO"
COMUNI_REGION_CODE_FIELD = "COD_REG"
COMUNI_REGION_NAME_FIELD = "NOME_REG"
COMUNI_YEAR_FIELD = "ANNO"

COMUNI_LIST_OUT_FIELDS = (
    COMUNI_NAME_FIELD,
    COMUNI_ISTAT_FIELD,
    COMUNI_PROVINCE_NAME_FIELD,
    COMUNI_PROVINCE_SHORT_FIELD,
    COMUNI_PROVINCE_CODE_FIELD,
)

COMUNI_DEFAULT_PAGE_SIZE = 1000
COMUNI_MIN_PAGE_SIZE = 1
COMUNI_MAX_PAGE_SIZE = 1000
COMUNI_DEFAULT_MAX_PAGES = 10
COMUNI_MIN_MAX_PAGES = 1
COMUNI_MAX_MAX_PAGES = 100
COMUNI_MIN_MAX_FEATURES = 1
COMUNI_QUERY_FORMAT = "geojson"


# ---------------------------------------------------------------------------
# Generic numeric coercion and pagination validators
# ---------------------------------------------------------------------------

def _coerce_number(value, label):
    """Coerce ``value`` into a finite float, raising on bool/inf/nan inputs."""
    if isinstance(value, bool):
        raise ValueError(f"Comuni {label} must be numeric, not a boolean value.")

    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Comuni {label} must be numeric.") from None

    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"Comuni {label} must be a finite numeric value.")

    return number


def validate_page_size(page_size):
    """Validate and return an ArcGIS REST page size for the Comuni layer."""
    if isinstance(page_size, bool):
        raise ValueError("Comuni page_size must be an integer, not a boolean value.")

    try:
        value = int(page_size)
    except (TypeError, ValueError):
        raise ValueError("Comuni page_size must be an integer.") from None

    if value < COMUNI_MIN_PAGE_SIZE or value > COMUNI_MAX_PAGE_SIZE:
        raise ValueError(
            "Comuni page_size must be between {} and {} records.".format(
                COMUNI_MIN_PAGE_SIZE,
                COMUNI_MAX_PAGE_SIZE,
            )
        )

    return value


def validate_offset(offset):
    """Validate and return a zero-based ArcGIS REST pagination offset."""
    if isinstance(offset, bool):
        raise ValueError("Comuni offset must be an integer, not a boolean value.")

    try:
        value = int(offset)
    except (TypeError, ValueError):
        raise ValueError("Comuni offset must be an integer.") from None

    if value < 0:
        raise ValueError("Comuni offset must be greater than or equal to zero.")

    return value


def validate_max_pages(max_pages):
    """Validate and return a maximum number of ArcGIS pages."""
    if isinstance(max_pages, bool):
        raise ValueError("Comuni max_pages must be an integer, not a boolean value.")

    try:
        value = int(max_pages)
    except (TypeError, ValueError):
        raise ValueError("Comuni max_pages must be an integer.") from None

    if value < COMUNI_MIN_MAX_PAGES or value > COMUNI_MAX_MAX_PAGES:
        raise ValueError(
            "Comuni max_pages must be between {} and {}.".format(
                COMUNI_MIN_MAX_PAGES,
                COMUNI_MAX_MAX_PAGES,
            )
        )

    return value


def validate_max_features(max_features):
    """Validate and return a maximum number of features to retain."""
    if max_features is None:
        return None

    if isinstance(max_features, bool):
        raise ValueError("Comuni max_features must be an integer, not a boolean value.")

    try:
        value = int(max_features)
    except (TypeError, ValueError):
        raise ValueError("Comuni max_features must be an integer.") from None

    if value < COMUNI_MIN_MAX_FEATURES:
        raise ValueError(
            "Comuni max_features must be greater than or equal to {}.".format(
                COMUNI_MIN_MAX_FEATURES
            )
        )

    return value


# ---------------------------------------------------------------------------
# Comuni-specific identifier validators
# ---------------------------------------------------------------------------

def validate_istat_code(value):
    """Validate and return an ISTAT municipality numeric code.

    The service exposes ``ISTAT`` as an integer. Accept ints or numeric strings
    and reject zero/negative values. The five-digit form (``015240``) is also
    accepted because the leading zero may have been dropped by upstream code.
    """
    if isinstance(value, bool):
        raise ValueError("Comuni ISTAT code must be an integer, not a boolean value.")

    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("Comuni ISTAT code must not be empty.")
        try:
            number = int(text)
        except ValueError:
            raise ValueError(f"Comuni ISTAT code is not a valid integer: '{value}'.") from None
    else:
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("Comuni ISTAT code must be an integer.") from None

    if number <= 0:
        raise ValueError("Comuni ISTAT code must be a positive integer.")

    return number


def validate_comune_name(value):
    """Validate and return a non-empty Comune name string."""
    if not isinstance(value, str):
        raise ValueError("Comune name must be a string.")

    text = value.strip()
    if not text:
        raise ValueError("Comune name must not be empty.")

    return text


_TITLE_LOWERCASE_TOKENS = frozenset(
    {"di", "da", "del", "della", "dei", "delle", "degli", "in", "su", "sul",
     "sulla", "e", "al", "alla", "ai", "alle", "agli", "con", "per"}
)

_APOSTROPHE_LOWERCASE_HEADS = frozenset(
    {"d", "l", "all", "dall", "dell", "nell", "sull", "un"}
)


_APOSTROPHE_VARIANTS = ("`", "‘", "’", "ʼ", "´")


def normalize_comune_display_name(value):
    """Return a human-friendly form of a RL service municipality name.

    The Lombardia ArcGIS REST service serves ``NOME_COM`` in uppercase and
    sometimes uses a backtick (`` ` ``) or other apostrophe variants instead
    of the ASCII apostrophe. For the user interface (autocomplete, alerts,
    logs) we want the conventional title-case form. Handled cases:

    - ``ZIBIDO SAN GIACOMO`` -> ``Zibido San Giacomo``
    - ``SAN GIORGIO SU LEGNANO`` -> ``San Giorgio su Legnano`` (linking
      preposition ``su`` stays lowercase when not the first word)
    - ``CASSANO D'ADDA`` -> ``Cassano d'Adda`` (preposition ``d'`` stays
      lowercase mid-name, but the following toponym is capitalised)
    - ``L'AQUILA`` -> ``L'Aquila`` (apostrophe head stays capitalised when
      it is the first word of the name)
    - ``ALBANO SANT`ALESSANDRO`` -> ``Albano Sant'Alessandro`` (backtick is
      normalised to a regular apostrophe before processing)
    """
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return text

    for variant in _APOSTROPHE_VARIANTS:
        text = text.replace(variant, "'")

    parts = []
    for index, word in enumerate(text.split()):
        if not word:
            continue

        word_lower = word.lower()

        if "'" in word:
            head, tail = word.split("'", 1)
            head_lower = head.lower()
            tail_converted = tail.capitalize()
            if index > 0 and head_lower in _APOSTROPHE_LOWERCASE_HEADS:
                converted = head_lower + "'" + tail_converted
            else:
                converted = head.capitalize() + "'" + tail_converted
        elif index > 0 and word_lower in _TITLE_LOWERCASE_TOKENS:
            converted = word_lower
        else:
            converted = word.capitalize()

        parts.append(converted)

    return " ".join(parts)


def _quote_sql_string(value):
    """Quote a value for inclusion in an ArcGIS REST ``where`` clause."""
    return "'" + str(value).replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# outFields validator
# ---------------------------------------------------------------------------

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
                    raise ValueError("Comuni out_fields values must be strings, not booleans.")
                fields.append(str(field).strip())
        except TypeError:
            raise ValueError("Comuni out_fields must be None, a string, or an iterable.") from None

    fields = [field for field in fields if field]

    if not fields:
        raise ValueError("Comuni out_fields must not be empty.")

    if "*" in fields:
        if len(fields) != 1:
            raise ValueError("Comuni out_fields '*' cannot be combined with named fields.")
        return "*"

    for field in fields:
        if not field.replace("_", "").isalnum():
            raise ValueError(f"Unsafe Comuni out_field name: {field}.")

    return ",".join(fields)


# ---------------------------------------------------------------------------
# Response and feature validators
# ---------------------------------------------------------------------------

def validate_arcgis_json_response(response_json):
    """Validate an ArcGIS JSON/GeoJSON response already loaded in memory."""
    if not isinstance(response_json, dict):
        raise ValueError("Comuni ArcGIS response must be a dictionary.")

    if "error" in response_json:
        error = response_json["error"]
        if isinstance(error, dict):
            message = error.get("message") or error.get("details") or error
        else:
            message = error
        raise ValueError(f"Comuni ArcGIS response contains an error: {message}.")

    if "features" in response_json and not isinstance(response_json["features"], list):
        raise ValueError("Comuni ArcGIS response field 'features' must be a list.")

    return response_json


def _attributes_container(feature):
    """Return ``(attributes, container_name)`` for an ArcGIS or GeoJSON feature."""
    if "attributes" in feature:
        return feature["attributes"], "attributes"
    if "properties" in feature:
        return feature["properties"], "properties"
    raise ValueError("Comuni feature is missing attributes/properties.")


def validate_comune_feature(feature):
    """Validate one Comune feature already loaded in memory.

    The feature must carry a non-null geometry and at least the Comune name
    and ISTAT code attributes. Additional attributes are tolerated.
    """
    if not isinstance(feature, dict):
        raise ValueError("Comuni feature must be a dictionary.")

    if "geometry" not in feature or feature["geometry"] is None:
        raise ValueError("Comuni feature is missing geometry.")

    attributes, container = _attributes_container(feature)
    if not isinstance(attributes, dict):
        raise ValueError(f"Comuni feature {container} must be a dictionary.")

    missing = [
        field for field in (COMUNI_NAME_FIELD, COMUNI_ISTAT_FIELD)
        if field not in attributes
    ]
    if missing:
        raise ValueError(
            "Comuni feature is missing required fields: {}.".format(", ".join(missing))
        )

    return feature


def validate_comune_list_entry(entry):
    """Validate one entry of the lightweight name/code list (no geometry)."""
    if not isinstance(entry, dict):
        raise ValueError("Comuni list entry must be a dictionary.")

    attributes, container = _attributes_container(entry)
    if not isinstance(attributes, dict):
        raise ValueError(f"Comuni list entry {container} must be a dictionary.")

    missing = [
        field for field in (COMUNI_NAME_FIELD, COMUNI_ISTAT_FIELD)
        if field not in attributes
    ]
    if missing:
        raise ValueError(
            "Comuni list entry is missing required fields: {}.".format(", ".join(missing))
        )

    return entry


def validate_comune_features(features):
    """Validate a list of Comune features without modifying it."""
    if not isinstance(features, list):
        raise ValueError("Comuni features must be a list.")

    for index, feature in enumerate(features):
        try:
            validate_comune_feature(feature)
        except ValueError as exc:
            raise ValueError("Invalid Comune feature at index {}: {}.".format(index, exc)) from exc

    return features


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Query specification dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArcGisQuerySpec:
    """Description of an ArcGIS REST query that may be executed later."""

    url: str
    params: dict

    def as_url(self):
        """Return the complete URL without executing the request."""
        return f"{self.url}?{urlencode(self.params)}"


# ---------------------------------------------------------------------------
# Notification, cancellation, network helpers
# ---------------------------------------------------------------------------

def _notify(callback=None, feedback=None, message=""):
    """Send an optional progress message without importing QGIS classes."""
    if callable(callback):
        callback(message)

    if feedback is not None and hasattr(feedback, "pushInfo"):
        feedback.pushInfo(message)


def _raise_if_canceled(feedback=None):
    """Raise a generic runtime error when an optional feedback object is canceled."""
    if feedback is not None and hasattr(feedback, "isCanceled") and feedback.isCanceled():
        raise RuntimeError("Comuni feature download canceled.")


def _read_json_url(url, timeout):
    """Read a JSON document from URL using the standard library only."""
    _validate_url_scheme(url)
    try:
        with urlopen(url, timeout=timeout) as response:  # nosec B310 - scheme validated above
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise ValueError(f"Comuni ArcGIS request failed with HTTP status {status}.")

            payload = response.read().decode("utf-8")

    except HTTPError as exc:
        raise ValueError(f"Comuni ArcGIS request failed with HTTP status {exc.code}.") from exc
    except URLError as exc:
        raise ValueError(f"Comuni ArcGIS network error: {exc.reason}.") from exc
    except TimeoutError as exc:
        raise ValueError("Comuni ArcGIS request timed out.") from exc
    except OSError as exc:
        raise ValueError(f"Comuni ArcGIS I/O error: {exc}.") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("Comuni ArcGIS response is not valid JSON.") from exc


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------

class LombardiaComuniClient:
    """Build and optionally execute REST queries against the RL Comuni layer.

    Network access happens only when ``fetch_*`` methods are called.
    The class is independent from QGIS Processing and does not touch any
    layer or project state.
    """

    service_url = COMUNI_SERVICE_URL
    layer_id = COMUNI_LAYER_ID
    layer_url = COMUNI_LAYER_URL
    expected_crs_authid = COMUNI_EXPECTED_CRS_AUTHID
    expected_crs_wkid = COMUNI_EXPECTED_CRS_WKID
    name_field = COMUNI_NAME_FIELD
    istat_field = COMUNI_ISTAT_FIELD
    default_page_size = COMUNI_DEFAULT_PAGE_SIZE

    def __init__(self, page_size=None):
        if page_size is None:
            page_size = self.default_page_size

        self.page_size = validate_page_size(page_size)

    def metadata(self):
        """Return static metadata expected by the wizard and the algorithm."""
        return {
            "service_url": self.service_url,
            "layer_url": self.layer_url,
            "layer_id": self.layer_id,
            "expected_crs_authid": self.expected_crs_authid,
            "required_fields": [self.name_field, self.istat_field],
            "list_out_fields": list(COMUNI_LIST_OUT_FIELDS),
            "page_size": self.page_size,
        }

    # ----- Query spec builders ------------------------------------------------

    def build_list_query_spec(self, offset=0, out_fields=None):
        """Build a query that returns the lightweight list of Comuni.

        No geometry is requested by default. Used to populate the autocomplete
        with NOME_COM, ISTAT and province metadata.
        """
        if out_fields is None:
            out_fields = COMUNI_LIST_OUT_FIELDS

        params = {
            "f": COMUNI_QUERY_FORMAT,
            "where": "1=1",
            "outFields": validate_out_fields(out_fields),
            "returnGeometry": "false",
            "resultRecordCount": self.page_size,
            "resultOffset": validate_offset(offset),
            "orderByFields": self.name_field,
        }

        return ArcGisQuerySpec(url=f"{self.layer_url}/query", params=params)

    def build_geometry_query_spec_by_istat(self, istat_code, out_fields=None):
        """Build a query that returns one Comune feature with full geometry."""
        istat_code = validate_istat_code(istat_code)

        params = {
            "f": COMUNI_QUERY_FORMAT,
            "where": f"{self.istat_field} = {istat_code}",
            "outFields": validate_out_fields(out_fields),
            "returnGeometry": "true",
            "outSR": str(COMUNI_EXPECTED_CRS_WKID),
            "resultRecordCount": 1,
            "resultOffset": 0,
        }

        return ArcGisQuerySpec(url=f"{self.layer_url}/query", params=params)

    def build_geometry_query_spec_by_name(self, comune_name, out_fields=None):
        """Build a query by Comune name with case-insensitive matching.

        The RL service stores ``NOME_COM`` in uppercase but the user-facing UI
        (and ISTAT) use mixed case. We compare ``UPPER(NOME_COM)`` against the
        uppercased input so the same builder works regardless of how the
        caller spelled the name.
        """
        comune_name = validate_comune_name(comune_name)

        params = {
            "f": COMUNI_QUERY_FORMAT,
            "where": "UPPER({}) = {}".format(
                self.name_field,
                _quote_sql_string(comune_name.upper()),
            ),
            "outFields": validate_out_fields(out_fields),
            "returnGeometry": "true",
            "outSR": str(COMUNI_EXPECTED_CRS_WKID),
            "resultRecordCount": 1,
            "resultOffset": 0,
        }

        return ArcGisQuerySpec(url=f"{self.layer_url}/query", params=params)

    # ----- Network operations -------------------------------------------------

    def fetch_comuni_list(
        self,
        out_fields=None,
        timeout=60,
        max_pages=None,
        max_features=None,
        callback=None,
        feedback=None,
    ):
        """Fetch the paginated lightweight list of Comuni from the REST service.

        Returns a list of GeoJSON feature dicts with attributes only (no
        geometry). The method honours ``feedback.isCanceled()`` between pages
        and never blocks on a single huge response thanks to ``page_size``.
        """
        timeout = _coerce_number(timeout, "timeout")
        if timeout <= 0:
            raise ValueError("Comuni timeout must be greater than zero.")

        if max_pages is None:
            max_pages = COMUNI_DEFAULT_MAX_PAGES
        max_pages = validate_max_pages(max_pages)
        max_features = validate_max_features(max_features)

        features = []
        current_offset = 0
        page_count = 0

        while True:
            _raise_if_canceled(feedback)

            if page_count >= max_pages:
                _notify(
                    callback=callback,
                    feedback=feedback,
                    message="Comuni fetch stopped after reaching max_pages={}; collected {}.".format(
                        max_pages,
                        len(features),
                    ),
                )
                break

            query_spec = self.build_list_query_spec(
                offset=current_offset,
                out_fields=out_fields,
            )
            _notify(
                callback=callback,
                feedback=feedback,
                message="Comuni fetch page {} at offset {}.".format(page_count + 1, current_offset),
            )

            response_json = validate_arcgis_json_response(
                _read_json_url(query_spec.as_url(), timeout=timeout)
            )

            if "features" not in response_json:
                raise ValueError(
                    "Comuni ArcGIS response does not contain a 'features' field."
                )

            page_features = response_json["features"]

            if max_features is not None:
                remaining = max_features - len(features)
                if remaining <= 0:
                    break

                features.extend(page_features[:remaining])

                if len(page_features) >= remaining:
                    page_count += 1
                    break
            else:
                features.extend(page_features)

            page_count += 1

            new_offset = next_offset(
                response_json,
                current_offset=current_offset,
                page_size=self.page_size,
            )
            if new_offset is None:
                break
            if new_offset <= current_offset:
                raise ValueError(
                    "Comuni pagination did not advance from offset {}.".format(current_offset)
                )
            current_offset = new_offset

        for entry in features:
            validate_comune_list_entry(entry)

        return features

    def fetch_comune_geometry(
        self,
        istat_code=None,
        comune_name=None,
        timeout=60,
        feedback=None,
    ):
        """Fetch a single Comune feature (geometry + attributes) by ISTAT or name.

        At least one of ``istat_code`` and ``comune_name`` must be provided.
        When both are set, ``istat_code`` wins because it is the safer key.
        Returns the GeoJSON feature dict, or ``None`` when no feature matches.
        """
        if istat_code is None and comune_name is None:
            raise ValueError("Provide either istat_code or comune_name.")

        timeout = _coerce_number(timeout, "timeout")
        if timeout <= 0:
            raise ValueError("Comuni timeout must be greater than zero.")

        if istat_code is not None:
            query_spec = self.build_geometry_query_spec_by_istat(istat_code)
        else:
            query_spec = self.build_geometry_query_spec_by_name(comune_name)

        _raise_if_canceled(feedback)

        response_json = validate_arcgis_json_response(
            _read_json_url(query_spec.as_url(), timeout=timeout)
        )

        features = response_json.get("features") or []
        if not features:
            return None

        feature = features[0]
        validate_comune_feature(feature)
        return feature
