from app.web import (_calibration_rows, _calibration_sections, directional_hit,
                     favorable_move)


def test_directional_hit_up():
    assert directional_hit("up", 2.5, -1.0) is True
    assert directional_hit("up", 1.9, -5.0) is False  # moved big, wrong way


def test_directional_hit_down():
    assert directional_hit("down", 0.5, -2.1) is True
    assert directional_hit("down", 3.0, -0.5) is False


def test_directional_hit_unclear_counts_either_way():
    assert directional_hit("unclear", 0.0, -2.5) is True
    assert directional_hit(None, 2.5, 0.0) is True
    assert directional_hit("unclear", 1.0, -1.0) is False


def test_favorable_move_is_signed_by_prediction():
    assert favorable_move("up", 3.0, -1.0) == 3.0
    assert favorable_move("down", 3.0, -4.0) == 4.0   # down 4% = +4% favorable
    assert favorable_move("up", 0.5, -6.0) == 0.5      # crash doesn't help a long


def test_calibration_separates_direction_from_any():
    # Predicted up, moved down 5%: an "any direction" hit but a directional miss.
    outcomes = [(80, 1, "up", 0.5, -5.0)]
    row = _calibration_rows(outcomes)
    assert "<td>70-84</td><td>1</td><td>0%</td><td>100%</td>" in row


def test_calibration_sections_split_by_source():
    outcomes = [
        ("news", 80, 1, "up", 3.0, -0.5),
        ("halt", 80, 0, "unclear", 20.0, -1.0),
        ("halt", 80, 0, "unclear", 15.0, -2.0),
    ]
    page = _calibration_sections(outcomes)
    assert "<h3>news</h3>" in page and "<h3>halt</h3>" in page
    # News appears before halt, and halt's big moves don't leak into news.
    assert page.index("<h3>news</h3>") < page.index("<h3>halt</h3>")
    news_part = page.split("<h3>halt</h3>")[0]
    assert "<td>70-84</td><td>1</td><td>100%</td>" in news_part
