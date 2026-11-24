# -*- coding: utf-8 -*-
"""桥接横断补齐链的回归夹具。

并发 HTTP 赛马后补出的洞：桥接立在中间缺日 Dm 上时，若去抽更晚的 Dn，
旧实现的同票补齐会沿链把 Dm 也物化成有效镜，造成「Dm 有效桥接 + Dm
有效镜」并存。正确口径：桥接横断整条补齐链，跨桥抽取整票回滚，
链上任何一日都不得新增有效镜；解除后才能补齐并回放守恒。
"""

import pytest

from app.db import 连接
from app.services import 断档
from app.services.断档 import 桥接阻断Error
from app.services.抽取 import 抽取单户

户 = "竞户链"
开户日 = "2027-03-01"
桥日 = "2027-03-03"
末日 = "2027-03-05"


def _建户与流水():
    conn = 连接()
    try:
        conn.execute(
            "INSERT INTO 账户(账户键, 户名, 币种, 开户营业日, 状态) "
            "VALUES (?,?, 'CNY', ?, '正常')",
            (户, "链断户", 开户日),
        )
        流水 = [
            ("l1", "2027-03-01", 1000),
            ("l2", "2027-03-02", -200),
            ("l3", "2027-03-03", 500),
            ("l4", "2027-03-04", 700),
            ("l5", "2027-03-05", -100),
        ]
        for 号, 日, 额 in 流水:
            conn.execute(
                "INSERT INTO 变动流水(流水号, 账户键, 营业日, 发生时, 变动额, 命运) "
                "VALUES (?,?,?, ?, ?, '已入账')",
                (号, 户, 日, f"{日}T01:00:00Z", 额),
            )
        conn.commit()
    finally:
        conn.close()


def _快照日(含非有效=False):
    sql = "SELECT 营业日 FROM 周期快照 WHERE 账户键=?"
    if not 含非有效:
        sql += " AND 状态='有效'"
    sql += " ORDER BY 营业日"
    conn = 连接()
    try:
        return [r["营业日"] for r in conn.execute(sql, (户,))]
    finally:
        conn.close()


def _并存数():
    conn = 连接()
    try:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM 断档桥接凭证 b "
            "JOIN 周期快照 s ON s.账户键=b.账户键 AND s.营业日=b.目标营业日 "
            "WHERE b.状态='有效' AND b.账户键=?",
            (户,),
        ).fetchone()["n"]
    finally:
        conn.close()


def test_中间日桥接_横断晚日补齐链_整票无镜_解除后守恒(client):
    _建户与流水()

    # 先在中间缺日 03-03 立桥。
    桥 = 断档.建立桥接(户, 桥日, "横断链回归")
    assert 桥["状态"] == "有效"

    # 桥接不阻断更早的日期：03-02 仍可同票补齐 03-01/02。
    r早 = 抽取单户(户, "2027-03-02")
    assert r早["结果"] in ("写入", "盖写")
    assert r早["补齐"] == ["2027-03-01"]
    assert _快照日() == ["2027-03-01", "2027-03-02"]

    # 服务层：跨桥抽 03-05 必须整拒。
    with pytest.raises(桥接阻断Error):
        抽取单户(户, 末日)

    # 同成败：回滚后不得新增 03-03/04/05 任何镜（含非有效行也不许留）。
    assert _快照日(含非有效=True) == ["2027-03-01", "2027-03-02"]
    assert _并存数() == 0

    # HTTP 层同样 409，且响应不是 200 半截成功。
    resp = client.post("/api/抽取", json={"账户键": 户, "营业日": 末日})
    assert resp.status_code == 409, resp.text
    assert _快照日(含非有效=True) == ["2027-03-01", "2027-03-02"]

    # 全局日终抽取把该户记为阻断，不产生该户晚日镜。
    g = client.post("/api/抽取", json={"营业日": 末日}).json()
    assert any(x["账户键"] == 户 and x["结果"] == "阻断" for x in g["明细"])
    assert "2027-03-05" not in _快照日()

    # 只读处置接口必须能看出「桥在链上、当日不可抽、invalid、无当日镜」。
    d = client.get(f"/api/断档处置?账户键={户}&营业日={末日}").json()
    assert d["处置"] == "桥接阻断"
    assert d["当日可否抽"] is False and d["canExtract"] is False
    assert d["valid"] is False and d["有效"] is False
    assert d["当日有效镜"] is False
    assert d["缺日"] == ["2027-03-03", "2027-03-04"]
    assert d["阻断桥接营业日"] == 桥日
    assert d["桥接横断链"] is True
    assert d["桥接凭证号"] == 桥["凭证号"]

    # 桥接目标日自身同样阻退且无镜。
    d桥日 = client.get(f"/api/断档处置?账户键={户}&营业日={桥日}").json()
    assert d桥日["当日可否抽"] is False and d桥日["当日有效镜"] is False

    # —— 解除后重抽：补齐 03-03/04 再写 03-05，余额链守恒 ——
    rel = client.post(
        "/api/断档桥接/解除", json={"账户键": 户, "营业日": 桥日}
    )
    assert rel.status_code == 200, rel.text

    ok = client.post("/api/抽取", json={"账户键": 户, "营业日": 末日})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["补齐"] == ["2027-03-03", "2027-03-04"]

    rows = {r["营业日"]: r for r in client.get("/api/快照").json() if r["账户键"] == 户}
    assert rows["2027-03-01"]["余额"] == 1000
    assert rows["2027-03-01"]["开端来源"] == "开户零"
    assert rows["2027-03-02"]["余额"] == 800
    assert rows["2027-03-03"]["余额"] == 1300
    assert rows["2027-03-03"]["开端来源"] == "前日快照"
    assert rows["2027-03-04"]["余额"] == 2000
    assert rows["2027-03-05"]["余额"] == 1900
    assert rows["2027-03-05"]["开端来源"] == "同票补齐"

    # 全库永不出现毒源标记「以零为开端」。
    conn = 连接()
    try:
        毒 = conn.execute(
            "SELECT COUNT(*) AS n FROM 周期快照 WHERE 开端来源='以零为开端'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert 毒 == 0

    # 末日回放守恒。
    rp = client.post("/api/回放", json={"营业日": 末日}).json()
    assert rp["通过"] is True, rp
    assert rp["差额"] == 0
