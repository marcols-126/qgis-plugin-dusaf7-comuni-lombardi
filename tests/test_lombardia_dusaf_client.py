# -*- coding: utf-8 -*-

"""Pure unit tests for LombardiaDusafClient validators."""

import pytest


class TestEnvelope:
    def test_accepts_dict(self, lombardia_dusaf_client):
        env = lombardia_dusaf_client.validate_envelope_32632(
            {"xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10}
        )
        assert env["xmin"] == 0
        assert env["xmax"] == 10
        assert env["spatialReference"]["wkid"] == 32632

    def test_accepts_sequence(self, lombardia_dusaf_client):
        env = lombardia_dusaf_client.validate_envelope_32632([100, 200, 300, 400])
        assert env["xmin"] == 100
        assert env["ymax"] == 400

    def test_rejects_xmin_ge_xmax(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_envelope_32632(
                {"xmin": 10, "ymin": 0, "xmax": 5, "ymax": 10}
            )

    def test_rejects_ymin_ge_ymax(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_envelope_32632(
                {"xmin": 0, "ymin": 10, "xmax": 10, "ymax": 5}
            )

    def test_rejects_missing_keys(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_envelope_32632(
                {"xmin": 0, "ymin": 0, "xmax": 10}
            )

    def test_rejects_wrong_length_sequence(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_envelope_32632([1, 2, 3])


class TestDusafLimits:
    def test_page_size_max(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client.validate_page_size(1000) == 1000

    def test_page_size_too_big(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_page_size(1001)

    def test_offset_negative(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_offset(-1)

    def test_max_pages_default_in_range(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client.validate_max_pages(50) == 50

    def test_max_features_none(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client.validate_max_features(None) is None

    def test_max_features_zero(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_max_features(0)


VALID_DUSAF_FEATURE = {
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
    "properties": {"COD_TOT": "1111", "DESCR": "tessuto residenziale denso"},
}


class TestDusafFeature:
    def test_valid(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client.validate_dusaf_feature(VALID_DUSAF_FEATURE) is VALID_DUSAF_FEATURE

    def test_rejects_missing_geometry(self, lombardia_dusaf_client):
        bad = dict(VALID_DUSAF_FEATURE)
        bad["geometry"] = None
        with pytest.raises(ValueError, match="geometry"):
            lombardia_dusaf_client.validate_dusaf_feature(bad)

    def test_rejects_missing_cod_tot(self, lombardia_dusaf_client):
        with pytest.raises(ValueError, match="COD_TOT"):
            lombardia_dusaf_client.validate_dusaf_feature({
                "geometry": VALID_DUSAF_FEATURE["geometry"],
                "properties": {"DESCR": "x"},
            })

    def test_rejects_missing_descr(self, lombardia_dusaf_client):
        with pytest.raises(ValueError, match="DESCR"):
            lombardia_dusaf_client.validate_dusaf_feature({
                "geometry": VALID_DUSAF_FEATURE["geometry"],
                "properties": {"COD_TOT": "1111"},
            })


class TestArcgisResponse:
    def test_accepts_empty(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client.validate_arcgis_json_response({}) == {}

    def test_rejects_error(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_arcgis_json_response(
                {"error": {"message": "broken"}}
            )

    def test_response_helpers_round_trip(self, lombardia_dusaf_client):
        page = {"features": [VALID_DUSAF_FEATURE], "exceededTransferLimit": True}
        assert lombardia_dusaf_client.response_has_features(page) is True
        assert lombardia_dusaf_client.response_exceeded_transfer_limit(page) is True


class TestQuerySpec:
    def test_metadata(self, lombardia_dusaf_client):
        client = lombardia_dusaf_client.LombardiaDusafClient()
        meta = client.metadata()
        assert meta["expected_crs_authid"] == "EPSG:32632"
        assert meta["layer_url"] == lombardia_dusaf_client.DUSAF_LAYER_URL
        assert meta["page_size"] == lombardia_dusaf_client.DUSAF_DEFAULT_PAGE_SIZE

    def test_build_without_geometry(self, lombardia_dusaf_client):
        client = lombardia_dusaf_client.LombardiaDusafClient()
        spec = client.build_query_spec(offset=0)
        assert spec.params["where"] == "1=1"
        assert "geometry" not in spec.params

    def test_build_with_envelope(self, lombardia_dusaf_client):
        client = lombardia_dusaf_client.LombardiaDusafClient()
        spec = client.build_query_spec(
            geometry={"xmin": 0, "ymin": 0, "xmax": 100, "ymax": 100},
            offset=0,
        )
        assert "geometry" in spec.params
        assert spec.params["geometryType"] == "esriGeometryEnvelope"
        assert spec.params["inSR"] == "32632"


class TestOutFields:
    def test_none_returns_star(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client.validate_out_fields(None) == "*"

    def test_list_joined(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client.validate_out_fields(["COD_TOT", "DESCR"]) == "COD_TOT,DESCR"

    def test_rejects_unsafe(self, lombardia_dusaf_client):
        with pytest.raises(ValueError):
            lombardia_dusaf_client.validate_out_fields(["BAD NAME"])


class TestEnvelopeTiling:
    def test_split_2x2_produces_4_tiles(self, lombardia_dusaf_client):
        env = {"xmin": 0.0, "ymin": 0.0, "xmax": 100.0, "ymax": 100.0}
        tiles = lombardia_dusaf_client._split_envelope_into_grid(env, 2)
        assert len(tiles) == 4

    def test_split_3x3_produces_9_tiles(self, lombardia_dusaf_client):
        env = {"xmin": 0.0, "ymin": 0.0, "xmax": 100.0, "ymax": 100.0}
        tiles = lombardia_dusaf_client._split_envelope_into_grid(env, 3)
        assert len(tiles) == 9

    def test_split_covers_full_envelope(self, lombardia_dusaf_client):
        env = {"xmin": 10.0, "ymin": 20.0, "xmax": 110.0, "ymax": 220.0}
        tiles = lombardia_dusaf_client._split_envelope_into_grid(env, 4)
        xmins = [t["xmin"] for t in tiles]
        xmaxs = [t["xmax"] for t in tiles]
        ymins = [t["ymin"] for t in tiles]
        ymaxs = [t["ymax"] for t in tiles]
        assert min(xmins) == 10.0
        assert max(xmaxs) == 110.0
        assert min(ymins) == 20.0
        assert max(ymaxs) == 220.0

    def test_split_1x1_returns_input(self, lombardia_dusaf_client):
        env = {"xmin": 5.0, "ymin": 6.0, "xmax": 15.0, "ymax": 16.0}
        tiles = lombardia_dusaf_client._split_envelope_into_grid(env, 1)
        assert tiles == [{"xmin": 5.0, "ymin": 6.0, "xmax": 15.0, "ymax": 16.0}]


class TestFeatureObjectId:
    def test_from_geojson_id(self, lombardia_dusaf_client):
        f = {"id": 42, "properties": {}, "geometry": {}}
        assert lombardia_dusaf_client._feature_object_id(f) == 42

    def test_from_properties_objectid(self, lombardia_dusaf_client):
        f = {"properties": {"OBJECTID": 100, "DESCR": "x"}}
        assert lombardia_dusaf_client._feature_object_id(f) == 100

    def test_from_attributes_objectid(self, lombardia_dusaf_client):
        f = {"attributes": {"OBJECTID": 7}}
        assert lombardia_dusaf_client._feature_object_id(f) == 7

    def test_none_when_missing(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client._feature_object_id({"properties": {"DESCR": "x"}}) is None

    def test_none_for_non_dict(self, lombardia_dusaf_client):
        assert lombardia_dusaf_client._feature_object_id("not a feature") is None
