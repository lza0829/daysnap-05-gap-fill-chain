# -*- coding: utf-8 -*-
"""跳日夹具：跳过中间日抽末日。

断言题目要求的可区分口径：
* 户甲（同票补齐）：有中间日镜 + 当日镜，当日镜开端来自补齐日；
* 户乙（桥接阻断）：无当日镜且有有效桥接，连中间日半截镜都没有；
* 解除后重抽两端，回放守恒（差额为零、批次通过）。
"""


def _快照行(client, 户, 日):
    rows = client.get(f"/api/快照?账户键={户}&营业日={日}&全部状态=true").json()
    return rows[0] if rows else None


def _有效镜(client, 户, 日):
    rows = client.get(f"/api/快照?账户键={户}&营业日={日}").json()
    return rows[0] if rows else None


def test_跳日夹具_甲补齐成镜_乙桥接无镜_解除重抽守恒(client):
    load = client.post("/api/装载夹具/跳日补齐样例.json")
    assert load.status_code == 200, load.text
    assert load.json()["入账"] == 7
    assert load.json()["预置桥接"][0]["状态"] == "有效"

    # 预置桥接确实落在 乙 × 11-22。
    桥 = client.get("/api/断档桥接?状态=有效").json()
    assert any(b["账户键"] == "户乙" and b["目标营业日"] == "2026-11-22" for b in 桥)

    # 先抽 11-20 锚点。
    a = client.post("/api/抽取", json={"营业日": "2026-11-20"})
    assert a.status_code == 200, a.text
    assert _有效镜(client, "户甲", "2026-11-20")["余额"] == 7500
    assert _有效镜(client, "户乙", "2026-11-20")["余额"] == 5000

    # 跳过 11-21，直接抽末日 11-22。
    r = client.post("/api/抽取", json={"营业日": "2026-11-22"})
    assert r.status_code == 200
    rep = r.json()

    # —— 户甲：事务内补齐中间日，甲有中间日 + 当日镜 ——
    甲中 = _有效镜(client, "户甲", "2026-11-21")
    甲末 = _有效镜(client, "户甲", "2026-11-22")
    assert 甲中 is not None and 甲末 is not None
    assert 甲中["来源"] == "同票补齐"
    assert 甲中["余额"] == 10500          # 7500 + 3000
    assert 甲末["余额"] == 9000           # 10500 - 1500
    assert 甲末["开端来源"] == "同票补齐"
    assert 甲末["开端余额"] == 甲中["余额"]

    # —— 户乙：桥接阻断，无当日有效镜 ——
    assert _有效镜(client, "户乙", "2026-11-22") is None
    # 同成败：整户事务回滚，不允许留下「断档已记 + 半截补齐镜」。
    assert _快照行(client, "户乙", "2026-11-21") is None
    assert any(x["账户键"] == "户乙" and x["结果"] == "阻断" for x in rep["明细"])

    d乙 = client.get("/api/断档处置?账户键=户乙&营业日=2026-11-22").json()
    assert d乙["处置"] == "桥接阻断"
    assert d乙["当日可否抽"] is False
    assert d乙["valid"] is False
    assert d乙["当日有效镜"] is False
    assert d乙["缺日"] == ["2026-11-21"]

    # 同一跳日运行后，甲乙「当日有效镜有无」必须可区分。
    d甲 = client.get("/api/断档处置?账户键=户甲&营业日=2026-11-22").json()
    assert d甲["当日有效镜"] is True and d甲["valid"] is True
    assert d乙["当日有效镜"] is False

    # 禁止毒源：全库不得出现显式「以零为开端」的快照开端标记。
    # （旧实现正是在跨断档时写零开端落有效镜。）
    # 通过快照接口逐户核对开端来源即可，DB 层不变量见并发用例。

    # —— 解除桥接 ——
    rel = client.post("/api/断档桥接/解除", json={"账户键": "户乙", "营业日": "2026-11-22"})
    assert rel.status_code == 200, rel.text

    # —— 重抽两端：中间日 11-21 与末日 11-22 ——
    e1 = client.post("/api/抽取", json={"账户键": "户乙", "营业日": "2026-11-21"})
    assert e1.status_code == 200 and e1.json()["结果"] in ("写入", "盖写")
    assert _有效镜(client, "户乙", "2026-11-21")["余额"] == 4000   # 5000 - 1000
    e2 = client.post("/api/抽取", json={"账户键": "户乙", "营业日": "2026-11-22"})
    assert e2.status_code == 200
    assert _有效镜(client, "户乙", "2026-11-22")["余额"] == 6000    # 4000 + 2000

    # 盖写重抽末日不得破坏守恒。
    assert client.post("/api/抽取", json={"营业日": "2026-11-22"}).status_code == 200

    # —— 最终可回放守恒：两端回放均通过、零差额 ——
    rp21 = client.post("/api/回放", json={"营业日": "2026-11-21"}).json()
    rp22 = client.post("/api/回放", json={"营业日": "2026-11-22"}).json()
    assert rp21["通过"] is True, rp21
    assert rp22["通过"] is True, rp22
    assert rp21["差额"] == 0 and rp22["差额"] == 0

    for 批 in (rp21["批次编号"], rp22["批次编号"]):
        rows = client.get(f"/api/差额?回放批次编号={批}").json()
        assert rows == []

    # 断档页依赖的旧断档记录接口仍可列出补齐处置。
    断 = client.get("/api/断档").json()
    assert any(r["账户键"] == "户甲" and r["缺营业日"] == "2026-11-21" and r["处置"] == "同票补齐" for r in 断)
