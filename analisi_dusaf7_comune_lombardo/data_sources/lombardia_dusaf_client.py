# -*- coding: utf-8 -*-

"""Client skeleton for Regione Lombardia DUSAF 7 ArcGIS REST data.

The module only stores endpoint metadata and conservative helper methods. It
does not perform network activity at import time and it is not integrated into
the current Processing workflow.
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
            "DUSAF ArcGIS URL has unsupported scheme '{}': only http/https allowed.".format(
                parsed.scheme
            )
        )
    return url


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
DUSAF_DEFAULT_MAX_PAGES = 50
DUSAF_MIN_MAX_PAGES = 1
DUSAF_MAX_MAX_PAGES = 10000
DUSAF_MIN_MAX_FEATURES = 1
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


def validate_max_pages(max_pages):
    """Validate and return a prudent maximum number of ArcGIS pages."""
    if isinstance(max_pages, bool):
        raise ValueError("DUSAF max_pages must be an integer, not a boolean value.")

    try:
        value = int(max_pages)
    except (TypeError, ValueError):
        raise ValueError("DUSAF max_pages must be an integer.") from None

    if value < DUSAF_MIN_MAX_PAGES or value > DUSAF_MAX_MAX_PAGES:
        raise ValueError(
            "DUSAF max_pages must be between {} and {}.".format(
                DUSAF_MIN_MAX_PAGES,
                DUSAF_MAX_MAX_PAGES,
            )
        )

    return value


def validate_max_features(max_features):
    """Validate and return a maximum number of features to keep in memory."""
    if max_features is None:
        return None

    if isinstance(max_features, bool):
        raise ValueError("DUSAF max_features must be an integer, not a boolean value.")

    try:
        value = int(max_features)
    except (TypeError, ValueError):
        raise ValueError("DUSAF max_features must be an integer.") from None

    if value < DUSAF_MIN_MAX_FEATURES:
        raise ValueError(
            "DUSAF max_features must be greater than or equal to {}.".format(
                DUSAF_MIN_MAX_FEATURES
            )
        )

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


def validate_dusaf_feature(feature):
    """Validate one DUSAF ArcGIS/GeoJSON feature already loaded in memory."""
    if not isinstance(feature, dict):
        raise ValueError("DUSAF feature must be a dictionary.")

    if "geometry" not in feature or feature["geometry"] is None:
        raise ValueError("DUSAF feature is missing geometry.")

    if "attributes" in feature:
        attributes = feature["attributes"]
        attribute_container = "attributes"
    elif "properties" in feature:
        attributes = feature["properties"]
        attribute_container = "properties"
    else:
        raise ValueError("DUSAF feature is missing attributes/properties.")

    if not isinstance(attributes, dict):
        raise ValueError(f"DUSAF feature {attribute_container} must be a dictionary.")

    missing = [
        field for field in (DUSAF_CLASS_FIELD, DUSAF_DESCRIPTION_FIELD)
        if field not in attributes
    ]
    if missing:
        raise ValueError(
            "DUSAF feature is missing required fields: {}.".format(", ".join(missing))
        )

    return feature


def validate_dusaf_features(features):
    """Validate a list of DUSAF features without modifying it."""
    if not isinstance(features, list):
        raise ValueError("DUSAF features must be a list.")

    for index, feature in enumerate(features):
        try:
            validate_dusaf_feature(feature)
        except ValueError as exc:
            raise ValueError("Invalid DUSAF feature at index {}: {}.".format(index, exc)) from exc

    return features


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


def _notify(callback=None, feedback=None, message=""):
    """Send an optional progress message without importing QGIS classes."""
    if callable(callback):
        callback(message)

    if feedback is not None and hasattr(feedback, "pushInfo"):
        feedback.pushInfo(message)


def _raise_if_canceled(feedback=None):
    """Raise a generic runtime error when an optional feedback object is canceled."""
    if feedback is not None and hasattr(feedback, "isCanceled") and feedback.isCanceled():
        raise RuntimeError("DUSAF feature download canceled.")


def _read_json_url(url, timeout):
    """Read a JSON document from URL using standard library only."""
    _validate_url_scheme(url)
    try:
        with urlopen(url, timeout=timeout) as response:  # nosec B310 - scheme validated above
            status = getattr(response, "status", 200)
            if status < 200 or status >= 300:
                raise ValueError(f"DUSAF ArcGIS request failed with HTTP status {status}.")

            payload = response.read().decode("utf-8")

    except HTTPError as exc:
        raise ValueError(f"DUSAF ArcGIS request failed with HTTP status {exc.code}.") from exc
    except URLError as exc:
        raise ValueError(f"DUSAF ArcGIS network error: {exc.reason}.") from exc
    except TimeoutError as exc:
        raise ValueError("DUSAF ArcGIS request timed out.") from exc
    except OSError as exc:
        raise ValueError(f"DUSAF ArcGIS I/O error: {exc}.") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("DUSAF ArcGIS response is not valid JSON.") from exc


_TRANSIENT_ERROR_HINTS = (
    "failed to execute query",
    "query failed",
    "timeout",
    "service unavailable",
    "internal server error",
    "i/o error",
    "network error",
    "request timed out",
)

_RETRY_MAX_ATTEMPTS = 5
_RETRY_BACKOFF_SECONDS = 3.0
_TILED_INTER_TILE_PAUSE_SECONDS = 0.8
_TILED_DEFERRED_RETRY_PAUSE_SECONDS = 30.0


def _is_transient_error(message):
    """Return True when a ValueError message looks like a retryable failure."""
    if not message:
        return False
    text = str(message).lower()
    return any(hint in text for hint in _TRANSIENT_ERROR_HINTS)


def _fetch_page_with_retry(url, timeout, callback=None, feedback=None, page_label=""):
    """Read + validate one DUSAF page with retry on transient server errors.

    The Regione Lombardia ArcGIS service occasionally returns a generic
    ``"Failed to execute query."`` error on heavy queries (large Comuni
    over many pagination steps) or on transient timeouts. We retry the
    same page up to ``_RETRY_MAX_ATTEMPTS`` times with an arithmetic
    backoff. Non-transient errors propagate immediately.
    """
    import time

    last_error = None
    for attempt in range(1, _RETRY_MAX_ATTEMPTS + 1):
        try:
            return validate_arcgis_json_response(_read_json_url(url, timeout=timeout))
        except ValueError as exc:
            last_error = exc
            if not _is_transient_error(str(exc)):
                raise

            if attempt >= _RETRY_MAX_ATTEMPTS:
                raise

            backoff = _RETRY_BACKOFF_SECONDS * attempt
            _notify(
                callback=callback,
                feedback=feedback,
                message=(
                    "DUSAF {label} tentativo {attempt}/{max_attempts} fallito "
                    "({exc}); ritento tra {backoff:.0f}s..."
                ).format(
                    label=page_label or "page",
                    attempt=attempt,
                    max_attempts=_RETRY_MAX_ATTEMPTS,
                    exc=str(exc).strip(),
                    backoff=backoff,
                ),
            )

            _raise_if_canceled(feedback)
            time.sleep(backoff)

    # Should never get here (loop returns or raises) but keep callers safe.
    if last_error is not None:
        raise last_error
    raise ValueError("DUSAF page retry exhausted without a definite error.")


def _split_envelope_into_grid(envelope, tiles_per_side):
    """Split a validated envelope into ``tiles_per_side x tiles_per_side`` cells.

    Returns a flat list of envelope dicts (no ``spatialReference``) suitable
    for passing back into ``validate_envelope_32632`` and the spatial query.
    The cells cover the original envelope exactly and do not overlap; features
    that fall on a shared boundary may be returned by two adjacent tiles and
    are deduplicated by the caller via OBJECTID.
    """
    xmin = float(envelope["xmin"])
    ymin = float(envelope["ymin"])
    xmax = float(envelope["xmax"])
    ymax = float(envelope["ymax"])

    width = xmax - xmin
    height = ymax - ymin
    dx = width / tiles_per_side
    dy = height / tiles_per_side

    tiles = []
    for col in range(tiles_per_side):
        for row in range(tiles_per_side):
            tile_xmin = xmin + col * dx
            tile_ymin = ymin + row * dy
            tile_xmax = xmin + (col + 1) * dx if col < tiles_per_side - 1 else xmax
            tile_ymax = ymin + (row + 1) * dy if row < tiles_per_side - 1 else ymax
            tiles.append(
                {
                    "xmin": tile_xmin,
                    "ymin": tile_ymin,
                    "xmax": tile_xmax,
                    "ymax": tile_ymax,
                }
            )
    return tiles


def _feature_object_id(feature):
    """Extract the OBJECTID of an ArcGIS / GeoJSON feature, or ``None``.

    Used by the tiled fetch to deduplicate features returned by multiple
    adjacent tiles when they intersect the shared boundary.
    """
    if not isinstance(feature, dict):
        return None

    candidate_id = feature.get("id")
    if candidate_id is not None:
        try:
            return int(candidate_id)
        except (TypeError, ValueError):
            pass

    for container in ("properties", "attributes"):
        attrs = feature.get(container)
        if isinstance(attrs, dict):
            for key in ("OBJECTID", "objectid", "OBJECTID_1"):
                if key in attrs and attrs[key] is not None:
                    try:
                        return int(attrs[key])
                    except (TypeError, ValueError):
                        return attrs[key]
    return None


class LombardiaDusafClient:
    """Prepare and optionally execute isolated DUSAF 7 ArcGIS REST requests.

    Network access happens only when ``fetch_features`` is called explicitly.
    The class is not integrated into the current Processing workflow.
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
            # Many ArcGIS REST services require an explicit orderByFields
            # for stable pagination on spatial queries; without it
            # ``resultOffset > 0`` returns a generic
            # "Failed to execute query." error on large Comuni.
            "orderByFields": "OBJECTID ASC",
        }

        if geometry is not None:
            geometry = validate_envelope_32632(geometry)
            params.update(
                {
                    "geometry": json.dumps(geometry, separators=(",", ":")),
                    "geometryType": "esriGeometryEnvelope",
                    "spatialRel": "esriSpatialRelIntersects",
                    "inSR": "32632",
                }
            )

        return ArcGisQuerySpec(url=f"{self.layer_url}/query", params=params)

    def fetch_features(
        self,
        geometry=None,
        out_fields=None,
        start_offset=0,
        max_pages=None,
        max_features=None,
        timeout=60,
        callback=None,
        feedback=None,
    ):
        """Fetch DUSAF features from ArcGIS REST when explicitly called.

        The method is intentionally isolated from the Processing workflow. It
        keeps all features in memory, writes no files, creates no directories,
        and uses only Python standard library networking.
        """
        current_offset = validate_offset(start_offset)
        timeout = _coerce_number(timeout, "timeout")

        if timeout <= 0:
            raise ValueError("DUSAF timeout must be greater than zero.")

        if max_pages is None:
            max_pages = DUSAF_DEFAULT_MAX_PAGES

        max_pages = validate_max_pages(max_pages)
        max_features = validate_max_features(max_features)

        features = []
        page_count = 0

        while True:
            _raise_if_canceled(feedback)

            if max_pages is not None and page_count >= max_pages:
                _notify(
                    callback=callback,
                    feedback=feedback,
                    message="DUSAF fetch stopped after reaching max_pages={}. ".format(max_pages)
                    + "{} features collected; more features may be available.".format(
                        len(features)
                    ),
                )
                break

            query_spec = self.build_query_spec(
                geometry=geometry,
                out_fields=out_fields,
                offset=current_offset,
            )
            _notify(
                callback=callback,
                feedback=feedback,
                message="DUSAF fetch page {} at offset {}.".format(page_count + 1, current_offset),
            )

            response_json = _fetch_page_with_retry(
                query_spec.as_url(),
                timeout=timeout,
                callback=callback,
                feedback=feedback,
                page_label="page {} at offset {}".format(page_count + 1, current_offset),
            )

            if "features" not in response_json:
                raise ValueError("DUSAF ArcGIS response does not contain a 'features' field.")

            page_features = response_json["features"]
            if max_features is not None:
                remaining = max_features - len(features)
                if remaining <= 0:
                    _notify(
                        callback=callback,
                        feedback=feedback,
                        message="DUSAF fetch stopped after reaching max_features={}.".format(
                            max_features
                        ),
                    )
                    break

                features.extend(page_features[:remaining])

                if len(page_features) >= remaining:
                    page_count += 1
                    _notify(
                        callback=callback,
                        feedback=feedback,
                        message="DUSAF fetch stopped after reaching max_features={}.".format(
                            max_features
                        ),
                    )
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
                    "DUSAF pagination did not advance from offset {}.".format(current_offset)
                )

            current_offset = new_offset

        return features

    def fetch_features_tiled(
        self,
        envelope,
        tiles_per_side,
        out_fields=None,
        max_pages=None,
        max_features_per_tile=None,
        timeout=60,
        callback=None,
        feedback=None,
    ):
        """Fetch DUSAF features by splitting an envelope into a grid of tiles.

        Fallback strategy for envelopes large enough to trigger the RL
        ArcGIS server's "Failed to execute query." on paginated reads.
        Each tile is queried as its own (typically single-page) spatial
        query; the per-tile feature lists are concatenated and deduplicated
        by ``OBJECTID`` (features that intersect the tile boundary appear
        in more than one tile).

        Args:
            envelope: ArcGIS REST envelope dict in EPSG:32632
                (xmin/ymin/xmax/ymax).
            tiles_per_side: Integer grid side. ``2`` produces 4 tiles,
                ``3`` produces 9, etc. Must be >= 1.
            out_fields, max_pages, timeout, callback, feedback:
                Forwarded to ``fetch_features`` for each tile. The page
                budget applies independently to each tile.
            max_features_per_tile: Optional cap on a single tile's
                feature count, mirroring ``max_features`` from
                ``fetch_features``.

        Returns:
            list[dict]: Deduplicated GeoJSON-shaped feature dicts.
        """
        if envelope is None:
            raise ValueError("DUSAF tiled fetch requires an envelope.")

        try:
            tiles_per_side = int(tiles_per_side)
        except (TypeError, ValueError):
            raise ValueError("tiles_per_side must be an integer >= 1.") from None
        if tiles_per_side < 1:
            raise ValueError("tiles_per_side must be >= 1.")

        import time

        envelope = validate_envelope_32632(envelope)
        tiles = _split_envelope_into_grid(envelope, tiles_per_side)

        all_features = []
        seen_object_ids = set()
        deferred = []
        tile_count = len(tiles)

        def _try_tile(index, tile, is_deferred=False):
            """Fetch one tile, dedupe and append. Returns True on success."""
            _raise_if_canceled(feedback)
            prefix = "DUSAF retry tile" if is_deferred else "DUSAF tile"
            _notify(
                callback=callback,
                feedback=feedback,
                message=(
                    "{prefix} {idx}/{total} bbox=({xmin:.0f},{ymin:.0f}; "
                    "{xmax:.0f},{ymax:.0f}) fetch..."
                ).format(
                    prefix=prefix,
                    idx=index,
                    total=tile_count,
                    xmin=tile["xmin"],
                    ymin=tile["ymin"],
                    xmax=tile["xmax"],
                    ymax=tile["ymax"],
                ),
            )

            tile_features = self.fetch_features(
                geometry=tile,
                out_fields=out_fields,
                start_offset=0,
                max_pages=max_pages,
                max_features=max_features_per_tile,
                timeout=timeout,
                callback=callback,
                feedback=feedback,
            )

            added = 0
            for feature in tile_features:
                object_id = _feature_object_id(feature)
                if object_id is None:
                    all_features.append(feature)
                    added += 1
                    continue
                if object_id in seen_object_ids:
                    continue
                seen_object_ids.add(object_id)
                all_features.append(feature)
                added += 1

            _notify(
                callback=callback,
                feedback=feedback,
                message="{prefix} {idx}/{total}: {count} feature ({added} nuove dopo dedup).".format(
                    prefix=prefix,
                    idx=index,
                    total=tile_count,
                    count=len(tile_features),
                    added=added,
                ),
            )
            return True

        # First pass: try every tile. Failures are deferred.
        for index, tile in enumerate(tiles, start=1):
            _raise_if_canceled(feedback)
            try:
                _try_tile(index, tile, is_deferred=False)
            except ValueError as exc:
                if not _is_transient_error(str(exc)):
                    raise
                _notify(
                    callback=callback,
                    feedback=feedback,
                    message=(
                        "DUSAF tile {idx}/{total}: ESAURITI tentativi. Verra' "
                        "ritentato in coda dopo pausa di {pause:.0f}s."
                    ).format(
                        idx=index,
                        total=tile_count,
                        pause=_TILED_DEFERRED_RETRY_PAUSE_SECONDS,
                    ),
                )
                deferred.append((index, tile))

            # Small pause between tiles to avoid hammering the server.
            if index < tile_count and _TILED_INTER_TILE_PAUSE_SECONDS > 0:
                _raise_if_canceled(feedback)
                time.sleep(_TILED_INTER_TILE_PAUSE_SECONDS)

        # Second pass: retry the deferred tiles after a longer cool-down.
        if deferred:
            _notify(
                callback=callback,
                feedback=feedback,
                message=(
                    "DUSAF retry coda: {count} tile da ritentare dopo "
                    "{pause:.0f}s di pausa..."
                ).format(
                    count=len(deferred),
                    pause=_TILED_DEFERRED_RETRY_PAUSE_SECONDS,
                ),
            )
            slept = 0.0
            while slept < _TILED_DEFERRED_RETRY_PAUSE_SECONDS:
                _raise_if_canceled(feedback)
                step = min(1.0, _TILED_DEFERRED_RETRY_PAUSE_SECONDS - slept)
                time.sleep(step)
                slept += step

            for index, tile in deferred:
                _try_tile(index, tile, is_deferred=True)

        return all_features

    def fetch_validated_features(
        self,
        geometry=None,
        out_fields=None,
        start_offset=0,
        max_pages=None,
        max_features=None,
        timeout=60,
        callback=None,
        feedback=None,
    ):
        """Fetch DUSAF features and validate their geometry and attributes.

        This is a conservative wrapper around ``fetch_features``. It keeps the
        raw method available, writes no files, creates no directories, and does
        not interact with QGIS Processing.
        """
        features = self.fetch_features(
            geometry=geometry,
            out_fields=out_fields,
            start_offset=start_offset,
            max_pages=max_pages,
            max_features=max_features,
            timeout=timeout,
            callback=callback,
            feedback=feedback,
        )

        try:
            return validate_dusaf_features(features)
        except ValueError as exc:
            raise ValueError("DUSAF validated fetch returned invalid features: {}.".format(exc)) from exc
