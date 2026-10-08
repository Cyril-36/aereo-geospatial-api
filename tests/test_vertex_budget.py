"""The file-wide vertex budget is checked before densifying but charged only on success."""

import numpy as np
import pytest

from app.services import measurements as m
from app.services.crs import kml_crs
from app.services.measurements import MeasurementLimits, Status, VertexBudget
from tests.helpers_measure import feature, line, run


def test_commit_rejects_impossible_counts():
    budget = VertexBudget(5)
    with pytest.raises(RuntimeError):
        budget.commit(-1)
    with pytest.raises(RuntimeError):
        budget.commit(6)
    budget.commit(5)
    assert budget.remaining == 0


def test_check_does_not_charge():
    budget = VertexBudget(5)
    budget.check(5)
    assert budget.remaining == 5


def test_laea_failure_after_reservation_releases_the_allowance(monkeypatch):
    real_apply = m._apply
    failed = []

    def fail_first_laea(geometry, transformer):
        out = real_apply(geometry, transformer)
        if "proj=laea" in transformer.definition and not failed:
            failed.append(True)
            return m.shapely.transform(out, lambda xy: np.full_like(xy, np.inf))
        return out

    monkeypatch.setattr(m, "_apply", fail_first_laea)
    # Each 10° meridian line needs 22 inserts; the file budget has room for exactly one.
    first, second = run(
        [feature(line([(0, 0), (0, 10)]), 0), feature(line([(1, 0), (1, 10)]), 1)],
        kml_crs(),
        MeasurementLimits(max_total_vertices=4 + 22),
    )
    assert (first.status, first.reason_code) == (Status.TRANSFORM_FAILED, "LAEA_PROJECTION_FAILED")
    assert first.generated_vertices == 0
    assert second.status == Status.MEASURED and second.generated_vertices == 22


def test_budget_is_still_enforced_across_successful_features():
    first, second = run(
        [feature(line([(0, 0), (0, 10)]), 0), feature(line([(1, 0), (1, 10)]), 1)],
        kml_crs(),
        MeasurementLimits(max_total_vertices=4 + 22),
    )
    assert first.status == Status.MEASURED and first.generated_vertices == 22
    assert (second.status, second.reason_code) == (Status.UNSUPPORTED_EXTENT, "DENSIFICATION_LIMIT")


def test_generated_vertices_are_points_actually_inserted():
    results = run(
        [
            feature(line([(0, 0), (0, 1)]), 0),  # 110.6 km -> 2
            feature(line([(10, 0), (10, 0.3)]), 1),  # 33 km -> 0
            feature({"type": "Point", "coordinates": [0, 0]}, 2),
            feature(line([(179, 0), (-179, 0)]), 3),  # antimeridian: not measured
            feature(line([("a", "b")]), 4),  # invalid
        ],
        kml_crs(),
    )
    assert [r.generated_vertices for r in results] == [2, 0, 0, 0, 0]
