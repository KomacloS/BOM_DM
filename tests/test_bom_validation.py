import pytest
from app.bom_schema import parse_bom


def test_bad_header():
    data = b"foo,bar\n1,2\n"
    with pytest.raises(ValueError):
        parse_bom(data)


def test_row_validation():
    data = b"part_number,description,qty,reference\nPN,Desc,1,R1\n"
    rows = parse_bom(data)
    assert rows[0].part_number == "PN"
