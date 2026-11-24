# -*- coding: utf-8 -*-
"""断档处置只读接口与桥接生命周期。

点名接口四要素：缺日、处置、当日可否抽、valid。
并验证两种口径下「首次遇断档后当日有效快照行是否存在」可区分：
同票补齐 -> 有行且 valid；桥接阻断 -> 无行、可抽=false、valid=false。
"""


def _快照(client, 户, 日):
    rows = client.get(f"/api/快照?账户键={户}&营业日={日}").json()
    return rows[0] if rows else None


def test_只读接口四字段_缺日随锚点收敛(client):
    # 全新库：甲无任何快照，抽 11-22 的缺口回溯到开户日 11-01。
    d = client.get("/api/断档处置?账户键=户甲&营业日=2026-11-22").json()
    for k in ("缺日", "处置", "当日可否抽", "valid"):
        assert k in d, f"只读处置接口缺字段 {k}"
    assert d["缺日"][0] == "2026-11-01"
    assert d["缺日"][-1] == "2026-11-21"
    assert len(d["缺日"]) == 21
    assert d["处置"] == "待补齐"
    assert d["当日可否抽"] is True
    assert d["valid"] is False

    # 只读：连查两次结果一致，且不产生任何快照/案件写入。
    d2 = client.get("/api/断档处置?账户键=户甲&营业日=2026-11-22").json()
    assert d2["缺日"] == d["缺日"] and d2["处置"] == d["处置"]
    assert client.get("/api/快照?账户键=户甲&营业日=2026-11-22").json() == []

    # 抽出 11-20 锚点（其链在同票内补齐到开户日）后，缺口收敛为中间日 11-21。
    r = client.post("/api/抽取", json={"营业日": "2026-11-20"})
    assert r.status_code == 200
    d3 = client.get("/api/断档处置?账户键=户甲&营业日=2026-11-22").json()
    assert d3["缺日"] == ["2026-11-21"]
    assert d3["当日可否抽"] is True and d3["valid"] is False

    # 路径形只读接口与查询形一致。
    d4 = client.get("/api/断档处置/户甲/2026-11-22").json()
    assert d4["缺日"] == ["2026-11-21"] and d4["valid"] is False


def test_同票补齐后_当日有效镜存在_开端来自补齐日(client):
    client.post("/api/抽取", json={"营业日": "2026-11-20"})
    r = client.post("/api/抽取", json={"账户键": "户甲", "营业日": "2026-11-22"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["结果"] == "写入"
    assert body["补齐"] == ["2026-11-21"]
    assert body["开端来源"] == "同票补齐"

    当日 = _快照(client, "户甲", "2026-11-22")
    中间 = _快照(client, "户甲", "2026-11-21")
    assert 当日 is not None and 中间 is not None
    assert 中间["来源"] == "同票补齐"
    assert 当日["开端来源"] == "同票补齐"
    assert 当日["开端余额"] == 中间["余额"]

    d = client.get("/api/断档处置?账户键=户甲&营业日=2026-11-22").json()
    assert d["处置"] == "已补齐"
    assert d["valid"] is True and d["当日有效镜"] is True
    assert d["缺日"] == []


def test_开户首日_合法零开端(client):
    # 丙开户日 2026-11-05：开户首日没有前日镜是合法零开端，不算断档。
    d = client.get("/api/断档处置?账户键=户丙&营业日=2026-11-05").json()
    assert d["缺日"] == []
    assert d["当日可否抽"] is True and d["valid"] is False

    r = client.post("/api/抽取", json={"账户键": "户丙", "营业日": "2026-11-05"})
    assert r.status_code == 200
    assert r.json()["开端来源"] == "开户零"
    d2 = client.get("/api/断档处置?账户键=户丙&营业日=2026-11-05").json()
    assert d2["valid"] is True and d2["处置"] == "无断档"


def test_桥接阻断_当日无有效镜_解除后可抽(client):
    r = client.post("/api/断档桥接", json={"账户键": "户甲", "营业日": "2026-12-01", "原因": "待核"})
    assert r.status_code == 200, r.text
    号 = r.json()["凭证号"]
    assert 号.startswith("桥-户甲-2026-12-01")

    # 重复立桥幂等。
    r2 = client.post("/api/断档桥接", json={"账户键": "户甲", "营业日": "2026-12-01"})
    assert r2.json()["幂等"] is True and r2.json()["凭证号"] == 号

    d = client.get("/api/断档处置?账户键=户甲&营业日=2026-12-01").json()
    assert d["处置"] == "桥接阻断"
    assert d["当日可否抽"] is False
    assert d["valid"] is False
    assert d["桥接凭证号"] == 号

    # 单户口抽取 -> 409 桥接阻断；全量抽取报告阻断但不影响他户。
    assert client.post("/api/抽取", json={"账户键": "户甲", "营业日": "2026-12-01"}).status_code == 409
    rep = client.post("/api/抽取", json={"营业日": "2026-12-01"}).json()
    assert any(x["账户键"] == "户甲" and x["结果"] == "阻断" for x in rep["明细"])

    # 硬断言：阻退后当日有效快照行不存在。
    assert _快照(client, "户甲", "2026-12-01") is None

    # 已有当日有效镜后再立阻断桥接 -> 409，不允许桥接与有效镜并存。
    assert client.post("/api/抽取", json={"账户键": "户丙", "营业日": "2026-11-05"}).status_code == 200
    conflict = client.post("/api/断档桥接", json={"账户键": "户丙", "营业日": "2026-11-05"})
    assert conflict.status_code == 409
    assert _快照(client, "户丙", "2026-11-05") is not None
    assert client.get("/api/断档桥接?状态=有效").json()  # 列表接口可用

    # 解除不存在的桥 -> 404。
    assert client.post("/api/断档桥接/解除", json={"凭证号": "桥-不存在"}).status_code == 404

    # 解除后桥接失效、可抽恢复，抽取成功且有镜。
    ok = client.post("/api/断档桥接/解除", json={"账户键": "户甲", "营业日": "2026-12-01"})
    assert ok.status_code == 200 and ok.json()["状态"] == "已解除"
    d2 = client.get("/api/断档处置?账户键=户甲&营业日=2026-12-01").json()
    assert d2["当日可否抽"] is True and d2["处置"] != "桥接阻断"
    assert client.post("/api/抽取", json={"账户键": "户甲", "营业日": "2026-12-01"}).status_code == 200
    assert _快照(client, "户甲", "2026-12-01") is not None


def test_集合只读接口_按营业日逐户返回(client):
    client.post("/api/抽取", json={"营业日": "2026-11-20"})
    rows = client.get("/api/断档处置?营业日=2026-11-22").json()
    assert isinstance(rows, list) and len(rows) == 3
    甲 = next(x for x in rows if x["账户键"] == "户甲")
    assert 甲["缺日"] == ["2026-11-21"]
    assert all("valid" in x and "当日可否抽" in x for x in rows)
