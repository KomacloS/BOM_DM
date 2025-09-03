import pytest
from app.logic.autofill_rules import infer_from_pn_and_desc


def test_package_inference():
    r = infer_from_pn_and_desc("C0603C104K5RAC", "")
    assert r.package == "0603"
    r = infer_from_pn_and_desc("", "CHIP CAP 2.2UF 16V 0603 T&R")
    assert r.package == "0603"
    r = infer_from_pn_and_desc("PN0603-0805", "mix 0603 0805")
    assert r.package is None


def test_cap_value_from_desc():
    r = infer_from_pn_and_desc("", "CHIP CAP 0.1UF 10% 50V")
    assert r.value == "0.1uF"


def test_cap_value_from_pn_eia():
    r = infer_from_pn_and_desc("C0402C101G5GACTU", "")
    assert r.value == "100pF"
    r = infer_from_pn_and_desc("C0603C106M", "")
    assert r.value == "10uF"


def test_res_value_from_desc():
    r = infer_from_pn_and_desc("", "RES 10K 1%")
    assert r.value == "10k"
    r = infer_from_pn_and_desc("", "RES 4R7 5%")
    assert r.value == "4.7Ω"


def test_ind_value_from_desc():
    r = infer_from_pn_and_desc("", "IND 4.7uH")
    assert r.value == "4.7uH"


def test_tolerance_from_desc():
    r = infer_from_pn_and_desc("", "CAP 0.1UF 10%")
    assert (r.tol_pos, r.tol_neg) == ("10", "10")
    r = infer_from_pn_and_desc("", "+10/-5% CAP")
    assert (r.tol_pos, r.tol_neg) == ("10", "5")


def test_tolerance_from_pn_letter():
    r = infer_from_pn_and_desc("C0603C104K5RAC", "CHIP CAP")
    assert (r.tol_pos, r.tol_neg) == ("10", "10")


def test_conflict_desc_vs_eia():
    r = infer_from_pn_and_desc("C0603C102K", "CAP 0.1UF")
    assert r.value == "0.1uF"
    r = infer_from_pn_and_desc("C0603C102K", "CAP CER")
    assert r.value == "1nF"
