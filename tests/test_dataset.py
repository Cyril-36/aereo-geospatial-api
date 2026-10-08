from app.services.dataset import iter_positions


def test_iter_positions_yields_numeric_positions():
    polygon = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    assert sorted(iter_positions(polygon)) == [[0, 0], [0, 0], [1, 0], [1, 1]]


def test_iter_positions_skips_malformed_nodes_instead_of_crashing():
    line = {"type": "LineString", "coordinates": [[0, 0], ["a", 1], 7, "text"]}
    assert list(iter_positions(line)) == [[0, 0]]
