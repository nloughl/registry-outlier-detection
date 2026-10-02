from regextract.parse import compact_numbers, parse_est_ci, parse_int, parse_median_iqr
from regextract.ocr import repair_est_ci


def test_est_ci_formats():
    assert parse_est_ci("1.9 (1.3, 2.8)")["lcl"] == 1.3                    # AOANJRR
    assert parse_est_ci("0.92(0.88-0.96)")["ucl"] == 0.96                  # NJR
    p = parse_est_ci("3.6 [2 .2; 5.0] (583)")                               # EPRD, split digits + at risk
    assert (p["estimate"], p["lcl"], p["ucl"], p["n_at_risk"]) == (3.6, 2.2, 5.0, 583)
    assert parse_est_ci("13.3 [1 1.5; 15.0] (588)")["lcl"] == 11.5
    assert parse_est_ci("19. 7 (16.3-23.7)")["estimate"] == 19.7            # SIRIS split word
    assert parse_est_ci("0.00 (.-.)")["status"] == "ci_not_estimable"
    assert parse_est_ci("n.a.")["status"] == "not_reported"
    assert parse_est_ci("")["status"] == "blank"


def test_int_and_iqr():
    assert parse_int("206,689") == (206689, [])
    assert parse_int("1,244*")[0] == 1244
    assert parse_median_iqr("64 (57 to 71)") == {"median": 64, "q1": 57, "q3": 71}
    assert parse_median_iqr("62 (56 - 70)")["q3"] == 70


def test_ocr_repair_restores_decimals():
    r = repair_est_ci("5.71(534-6.08)")
    assert (r["estimate"], r["lcl"], r["ucl"]) == (5.71, 5.34, 6.08)
    r = repair_est_ci("1245(11.95- 1294)")
    assert (r["estimate"], r["lcl"], r["ucl"]) == (12.45, 11.95, 12.94)
    assert repair_est_ci("na.")["status"] == "not_reported"


def test_suppressed_count_and_age_labels():
    from regextract.hooks import std_age
    assert parse_int("<4") == (None, ["suppressed<4"])
    assert [std_age(a) for a in ("<55", "55 to 64", "55-64", "≥75")] == ["<55", "55-64", "55-64", ">=75"]


def test_nth_header_occurrence():
    from regextract.grid import find_phrase
    from regextract.words import Word
    line = [Word("N", 10, 15, 0, 8), Word("1", 20, 24, 0, 8), Word("year", 25, 40, 0, 8),
            Word("N", 110, 115, 0, 8), Word("1", 120, 124, 0, 8), Word("year", 125, 140, 0, 8)]
    assert find_phrase([line], "1 year#2")[0] == 120
    assert find_phrase([line], "N#2")[0] == 110
