# -*- coding: utf-8 -*-

"""Pure unit tests for LombardiaComuniClient validators and helpers.

The module is loaded via ``conftest.py`` fixture to bypass the
``data_sources/__init__.py`` import chain (which imports QGIS classes).
"""

import pytest


# ---------------------------------------------------------------------------
# Page size, offset, max_pages, max_features
# ---------------------------------------------------------------------------

class TestPageSize:
    def test_accepts_valid_int(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_page_size(500) == 500

    def test_accepts_numeric_string(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_page_size("100") == 100

    def test_rejects_bool(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_page_size(True)

    def test_rejects_below_min(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_page_size(0)

    def test_rejects_above_max(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_page_size(10000)

    def test_rejects_non_numeric(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_page_size("abc")


class TestOffset:
    def test_accepts_zero(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_offset(0) == 0

    def test_accepts_positive(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_offset(1500) == 1500

    def test_rejects_negative(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_offset(-1)


class TestMaxPages:
    def test_accepts_valid(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_max_pages(5) == 5

    def test_rejects_below_min(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_max_pages(0)


class TestMaxFeatures:
    def test_none_passes_through(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_max_features(None) is None

    def test_accepts_positive(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_max_features(42) == 42

    def test_rejects_below_min(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_max_features(0)


# ---------------------------------------------------------------------------
# Comune identifiers
# ---------------------------------------------------------------------------

class TestIstatCode:
    def test_accepts_int(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_istat_code(15247) == 15247

    def test_accepts_numeric_string(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_istat_code("015247") == 15247

    def test_rejects_zero(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_istat_code(0)

    def test_rejects_negative(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_istat_code(-1)

    def test_rejects_empty_string(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_istat_code("")

    def test_rejects_bool(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_istat_code(True)


class TestComuneName:
    def test_strips_whitespace(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_comune_name("  Milano  ") == "Milano"

    def test_rejects_empty(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_comune_name("   ")

    def test_rejects_non_string(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_comune_name(123)


# ---------------------------------------------------------------------------
# Out fields
# ---------------------------------------------------------------------------

class TestOutFields:
    def test_none_returns_star(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_out_fields(None) == "*"

    def test_star_alone(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_out_fields("*") == "*"

    def test_star_cannot_combine(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_out_fields(["*", "NOME_COM"])

    def test_list_joined(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_out_fields(["NOME_COM", "ISTAT"]) == "NOME_COM,ISTAT"

    def test_string_split(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_out_fields("NOME_COM, ISTAT") == "NOME_COM,ISTAT"

    def test_rejects_unsafe_field_name(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_out_fields(["NOME COM"])


# ---------------------------------------------------------------------------
# ArcGIS response validation
# ---------------------------------------------------------------------------

class TestArcgisResponse:
    def test_rejects_non_dict(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_arcgis_json_response("not a dict")

    def test_propagates_error_field(self, lombardia_comuni_client):
        with pytest.raises(ValueError, match="errore"):
            lombardia_comuni_client.validate_arcgis_json_response({"error": {"message": "test errore"}})

    def test_accepts_empty_dict(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_arcgis_json_response({}) == {}

    def test_rejects_non_list_features(self, lombardia_comuni_client):
        with pytest.raises(ValueError):
            lombardia_comuni_client.validate_arcgis_json_response({"features": "nope"})


# ---------------------------------------------------------------------------
# Feature validation
# ---------------------------------------------------------------------------

VALID_COMUNE_GEOJSON_FEATURE = {
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
    "properties": {"NOME_COM": "ZIBIDO SAN GIACOMO", "ISTAT": 15247},
}

VALID_COMUNE_ARCGIS_FEATURE = {
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
    "attributes": {"NOME_COM": "ZIBIDO SAN GIACOMO", "ISTAT": 15247},
}


class TestComuneFeature:
    def test_valid_geojson(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_comune_feature(VALID_COMUNE_GEOJSON_FEATURE) is VALID_COMUNE_GEOJSON_FEATURE

    def test_valid_arcgis(self, lombardia_comuni_client):
        assert lombardia_comuni_client.validate_comune_feature(VALID_COMUNE_ARCGIS_FEATURE) is VALID_COMUNE_ARCGIS_FEATURE

    def test_rejects_missing_geometry(self, lombardia_comuni_client):
        with pytest.raises(ValueError, match="geometry"):
            lombardia_comuni_client.validate_comune_feature(
                {"geometry": None, "properties": {"NOME_COM": "x", "ISTAT": 1}}
            )

    def test_rejects_missing_name(self, lombardia_comuni_client):
        with pytest.raises(ValueError, match="NOME_COM"):
            lombardia_comuni_client.validate_comune_feature({
                "geometry": VALID_COMUNE_GEOJSON_FEATURE["geometry"],
                "properties": {"ISTAT": 1},
            })

    def test_list_entry_does_not_require_geometry(self, lombardia_comuni_client):
        entry = {"properties": {"NOME_COM": "MILANO", "ISTAT": 15146}}
        assert lombardia_comuni_client.validate_comune_list_entry(entry) is entry


# ---------------------------------------------------------------------------
# Pagination helpers
# ---------------------------------------------------------------------------

class TestPagination:
    def test_response_has_features(self, lombardia_comuni_client):
        assert lombardia_comuni_client.response_has_features({"features": [VALID_COMUNE_GEOJSON_FEATURE]}) is True

    def test_response_has_no_features(self, lombardia_comuni_client):
        assert lombardia_comuni_client.response_has_features({"features": []}) is False

    def test_exceeded_transfer_limit(self, lombardia_comuni_client):
        assert lombardia_comuni_client.response_exceeded_transfer_limit({"exceededTransferLimit": True}) is True

    def test_not_exceeded(self, lombardia_comuni_client):
        assert lombardia_comuni_client.response_exceeded_transfer_limit({}) is False

    def test_next_offset_advances_when_full_page(self, lombardia_comuni_client):
        page = {"features": [{} for _ in range(1000)], "exceededTransferLimit": True}
        offset = lombardia_comuni_client.next_offset(page, current_offset=0, page_size=1000)
        assert offset == 1000

    def test_next_offset_stops_when_empty(self, lombardia_comuni_client):
        assert lombardia_comuni_client.next_offset({"features": []}, current_offset=0, page_size=1000) is None

    def test_next_offset_stops_when_partial_page(self, lombardia_comuni_client):
        page = {"features": [{} for _ in range(200)]}
        assert lombardia_comuni_client.next_offset(page, current_offset=0, page_size=1000) is None


# ---------------------------------------------------------------------------
# Display name normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("ZIBIDO SAN GIACOMO", "Zibido San Giacomo"),
    ("MILANO", "Milano"),
    ("CASSANO D'ADDA", "Cassano d'Adda"),
    ("L'AQUILA", "L'Aquila"),
    ("SAN GIORGIO SU LEGNANO", "San Giorgio su Legnano"),
    ("SANTA MARIA DELLA VERSA", "Santa Maria della Versa"),
    ("VEDANO AL LAMBRO", "Vedano al Lambro"),
    ("MONTEROSSO AL MARE", "Monterosso al Mare"),
    ("BAGNOLO SAN VITO", "Bagnolo San Vito"),
    ("REGGIO NELL'EMILIA", "Reggio nell'Emilia"),
    ("ALBANO SANT`ALESSANDRO", "Albano Sant'Alessandro"),
    ("", ""),
    ("   ", ""),
])
def test_normalize_display_name(lombardia_comuni_client, raw, expected):
    assert lombardia_comuni_client.normalize_comune_display_name(raw) == expected


def test_normalize_display_name_non_string(lombardia_comuni_client):
    assert lombardia_comuni_client.normalize_comune_display_name(None) is None
    assert lombardia_comuni_client.normalize_comune_display_name(42) == 42


# ---------------------------------------------------------------------------
# Query spec builders
# ---------------------------------------------------------------------------

class TestQuerySpec:
    def test_default_metadata(self, lombardia_comuni_client):
        client = lombardia_comuni_client.LombardiaComuniClient()
        meta = client.metadata()
        assert meta["expected_crs_authid"] == "EPSG:32632"
        assert meta["layer_url"] == lombardia_comuni_client.COMUNI_LAYER_URL
        assert meta["page_size"] == lombardia_comuni_client.COMUNI_DEFAULT_PAGE_SIZE

    def test_build_list_query_spec(self, lombardia_comuni_client):
        client = lombardia_comuni_client.LombardiaComuniClient()
        spec = client.build_list_query_spec(offset=2000)
        assert spec.params["resultOffset"] == 2000
        assert spec.params["returnGeometry"] == "false"
        assert "NOME_COM" in spec.params["outFields"]
        assert spec.params["orderByFields"] == lombardia_comuni_client.COMUNI_NAME_FIELD

    def test_build_geometry_query_by_istat(self, lombardia_comuni_client):
        client = lombardia_comuni_client.LombardiaComuniClient()
        spec = client.build_geometry_query_spec_by_istat(15247)
        assert "ISTAT = 15247" in spec.params["where"]
        assert spec.params["returnGeometry"] == "true"
        assert spec.params["outSR"] == "32632"

    def test_build_geometry_query_by_name_uppercases(self, lombardia_comuni_client):
        client = lombardia_comuni_client.LombardiaComuniClient()
        spec = client.build_geometry_query_spec_by_name("Zibido San Giacomo")
        assert "UPPER(NOME_COM)" in spec.params["where"]
        assert "'ZIBIDO SAN GIACOMO'" in spec.params["where"]
