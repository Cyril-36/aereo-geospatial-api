"""Record mapping rules that real uploads cannot easily reach."""

import math

import pytest

from app.models import FileRecord
from app.results import apply_results, finite
from app.services.crs import kml_crs, resolve
from app.services.dataset import Dataset, RawFeature
from app.services.measurements import FeatureMeasurement, MeasuredDataset
from app.services.processor import ProcessedFile


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_floats_are_never_stored(value):
    assert finite(value) is None


@pytest.mark.parametrize("value", [0.0, 1.5, -2.0, None])
def test_finite_values_and_none_pass_through(value):
    assert finite(value) == value


def test_apply_results_keeps_zero_and_drops_non_finite():
    dataset = Dataset(
        "KML",
        kml_crs(),
        [RawFeature(0, None, "LineString", None, {}), RawFeature(1, None, "LineString", None, {})],
    )
    measured = MeasuredDataset(
        resolve(kml_crs()),
        [
            FeatureMeasurement(0, "MEASURED", length_m=0.0, geodesic_length_m=0.0),
            FeatureMeasurement(1, "MEASURED", length_m=math.nan, relative_difference=math.inf),
        ],
        [],
    )
    record = FileRecord(id="x", warnings=[])
    apply_results(record, ProcessedFile(dataset, measured))
    zero, bad = record.features
    assert (zero.length_m, zero.geodesic_length_m) == (0.0, 0.0)  # a calculated zero stays 0
    assert (bad.length_m, bad.relative_difference) == (None, None)
    assert record.status_counts == {"MEASURED": 2}
