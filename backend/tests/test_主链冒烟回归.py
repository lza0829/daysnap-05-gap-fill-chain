# -*- coding: utf-8 -*-
"""主链回归：三日连环样例顺序抽取 + 回放，改造后仍须守恒通过。"""


def test_三日连环_顺序抽取_回放零差额(client):
    r = client.post("/api/装载夹具/三日连环样例.json")
    assert r.status_code == 200
    for 日 in ("2026-11-20", "2026-11-21", "2026-11-22"):
        assert client.post("/api/抽取", json={"营业日": 日}).status_code == 200

    rp = client.post("/api/回放", json={"营业日": "2026-11-22"}).json()
    assert rp["通过"] is True, rp
    assert rp["差额"] == 0

    # 一键冒烟等价路径：重置后装样例再跑。
    client.post("/api/重置业务库")
    client.post("/api/装载夹具/三日连环样例.json")
    client.post("/api/抽取", json={"营业日": "2026-11-20"})
    client.post("/api/抽取", json={"营业日": "2026-11-21"})
    client.post("/api/抽取", json={"营业日": "2026-11-22"})
    rp22 = client.post("/api/回放", json={"营业日": "2026-11-22"}).json()
    assert rp22["通过"] is True and rp22["差额"] == 0

    总 = client.get("/api/总览").json()
    assert 总["快照数"] > 0
