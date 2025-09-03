from app.logic.autofill_rules import infer_from_pn_and_desc


def test_autofill_only_fills_empty():
    rows = [
        {
            "pn": "C0603C104K5RAC",
            "desc": "CHIP CAP 0.1UF 10% 0603",
            "package": "",
            "value": "",
            "tol_pos": "",
            "tol_neg": "",
        },
        {
            "pn": "R0603-10K",
            "desc": "RES 10K 1%",
            "package": "0603",
            "value": "1k",
            "tol_pos": "",
            "tol_neg": "",
        },
    ]

    for row in rows:
        res = infer_from_pn_and_desc(row["pn"], row["desc"])
        if res.package and not row["package"]:
            row["package"] = res.package
        if res.value and not row["value"]:
            row["value"] = res.value
        if res.tol_pos and res.tol_neg and not row["tol_pos"] and not row["tol_neg"]:
            row["tol_pos"] = res.tol_pos
            row["tol_neg"] = res.tol_neg

    assert rows[0]["package"] == "0603" and rows[0]["value"] == "0.1uF"
    assert rows[0]["tol_pos"] == "10" and rows[0]["tol_neg"] == "10"
    # Second row retains existing non-empty fields
    assert rows[1]["package"] == "0603"
    assert rows[1]["value"] == "1k"
