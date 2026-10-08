from samantha.cognition.charter import CHARTER


def test_charter_names_declared_knobs_not_earned_trust():
    assert "earned trust" not in CHARTER
    assert "samantha.expose" in CHARTER
    assert "# daemon:" in CHARTER
