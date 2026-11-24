# -*- coding: utf-8 -*-
"""处置与抽取并发夹具。

每轮新建一个「开户日 = 目标日前一日」的隔离竞户，用 Barrier 同时放行
「立断档桥接」与「同票补齐抽取」两个线程。SQLite BEGIN IMMEDIATE 把两个
写事务串行化，后到者在锁内复检先到者的提交，因此任意调度结果下都必须满足：

1. 有效桥接与当日有效镜永不并存（题目点名的并发不变量）；
2. 桥接胜 -> 抽取抛桥接阻断、当日有效镜不存在；
3. 抽取胜 -> 立桥接抛状态冲突，当日有效镜存在且开端链连续；
4. 全库不存在 开端来源='以零为开端' 的有效快照；
5. 非开户日的当日有效镜，其前一日有效镜必然在位（链连续，无跨断档零开端）。
"""

import threading
from datetime import date, timedelta

from app.db import 连接
from app.services import 抽取, 断档


def _建竞户(键: str, 开户日: str) -> None:
    conn = 连接()
    try:
        conn.execute(
            "INSERT INTO 账户(账户键, 户名, 币种, 开户营业日, 状态) "
            "VALUES (?,?, 'CNY', ?, '正常')",
            (键, f"竞户{键}", 开户日),
        )
        conn.commit()
    finally:
        conn.close()


def _库状态(键: str, 日: str):
    conn = 连接()
    try:
        桥 = conn.execute(
            "SELECT 凭证号 FROM 断档桥接凭证 "
            "WHERE 账户键=? AND 目标营业日=? AND 状态='有效'",
            (键, 日),
        ).fetchone()
        镜 = conn.execute(
            "SELECT 余额, 开端来源, 开端余额 FROM 周期快照 "
            "WHERE 账户键=? AND 营业日=? AND 状态='有效'",
            (键, 日),
        ).fetchone()
        零开端 = conn.execute(
            "SELECT COUNT(*) AS c FROM 周期快照 WHERE 状态='有效' AND 开端来源='以零为开端'"
        ).fetchone()["c"]
        前日镜 = None
        if 镜 is not None:
            y, m, d = map(int, 日.split("-"))
            前 = (date(y, m, d) - timedelta(days=1)).isoformat()
            前日镜 = conn.execute(
                "SELECT 快照键 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                (键, 前),
            ).fetchone()
        return (dict(桥) if 桥 else None), (dict(镜) if 镜 else None), int(零开端), 前日镜
    finally:
        conn.close()


def test_立桥接_vs_抽取_并发_绝无桥接与有效镜并存():
    轮数 = 12
    观察 = {"桥接胜": 0, "抽取胜": 0}
    基准 = date(2027, 1, 10)

    for k in range(轮数):
        日d = 基准 + timedelta(days=k)
        目标日 = 日d.isoformat()
        开户日 = (日d - timedelta(days=1)).isoformat()
        键 = f"竞户{k}"
        _建竞户(键, 开户日)

        闸 = threading.Barrier(2)
        果: dict[str, str] = {}

        def 跑抽取():
            闸.wait()
            try:
                抽取.抽取单户(键, 目标日, f"竞抽-{k}")
                果["抽"] = "ok"
            except 断档.桥接阻断Error:
                果["抽"] = "blocked"
            except Exception as e:  # noqa: BLE001 - 测试要看见任何意外异常
                果["抽"] = f"ERR:{type(e).__name__}:{e}"

        def 跑桥接():
            闸.wait()
            try:
                断档.建立桥接(键, 目标日, "竞速立桥")
                果["桥"] = "ok"
            except 断档.状态冲突Error:
                果["桥"] = "conflict"
            except Exception as e:  # noqa: BLE001
                果["桥"] = f"ERR:{type(e).__name__}:{e}"

        t抽 = threading.Thread(target=跑抽取, name=f"抽-{k}")
        t桥 = threading.Thread(target=跑桥接, name=f"桥-{k}")
        # 交替启动顺序，减少调度偏向。
        if k % 2 == 0:
            t抽.start(); t桥.start()
        else:
            t桥.start(); t抽.start()
        t抽.join(timeout=30)
        t桥.join(timeout=30)
        assert not t抽.is_alive() and not t桥.is_alive(), "并发线程超时未结束"

        # 不允许锁/业务以外的意外错误。
        assert 果.get("抽") in ("ok", "blocked"), f"轮{k} 抽取异常: {果}"
        assert 果.get("桥") in ("ok", "conflict"), f"轮{k} 桥接异常: {果}"

        桥, 镜, 零开端数, 前日镜 = _库状态(键, 目标日)

        # 不变量 4：全库任何轮次后都不得出现被禁用的零开端标记。
        assert 零开端数 == 0

        # 不变量 1：桥接与当日有效镜永不并存。
        assert not (桥 is not None and 镜 is not None), f"轮{k} 出现桥接+有效镜并存"

        if 桥 is not None:
            # 口径 B 分支：桥接先成交 -> 抽取必须被阻退且无当日镜。
            观察["桥接胜"] += 1
            assert 镜 is None, f"轮{k} 桥接存时竟有当日有效镜"
            assert 果["抽"] == "blocked", 果
            assert 果["桥"] == "ok", 果
        else:
            # 口径 A 分支：抽取先成交 -> 当日镜在，桥接必须因冲突未立成。
            观察["抽取胜"] += 1
            assert 镜 is not None, f"轮{k} 无桥接却也无当日镜: {果}"
            assert 果["抽"] == "ok", 果
            assert 果["桥"] == "conflict", 果
            assert 镜["开端来源"] in ("开户零", "同票补齐", "前日快照")
            # 目标日 > 开户日，前一日（本轮开户日）镜必须同票物化在位。
            assert 前日镜 is not None, f"轮{k} 当日镜存在但前日链断裂"

    # 12 轮里两种串行结局至少应能观察到；若调度极端偏向，用例本身仍通过不变量，
    # 这里只做信息记录，不把分支覆盖变成潜在 flaky 断言。
    print("并发结局分布:", 观察)


def test_全局抽取与立桥接并发_不产生毒镜():
    """更粗粒度：多线程反复对同一批户发起全局抽取与立/解桥，扫全库不变量。"""
    闸 = threading.Barrier(3)
    错误: list[Exception] = []

    def 全局抽():
        try:
            闸.wait()
            for i in range(5):
                抽取.日终抽取("2026-11-20", f"并发全局抽-{i}")
        except Exception as e:  # noqa: BLE001
            错误.append(e)

    def 立桥():
        try:
            闸.wait()
            for _ in range(5):
                try:
                    # 与全局抽取同一个 户甲 × 11-20 反复竞速：
                    # 桥先成则抽取被阻，镜先成则立桥冲突，二者永不并存。
                    断档.建立桥接("户甲", "2026-11-20", "全局竞速")
                except 断档.状态冲突Error:
                    pass
        except Exception as e:  # noqa: BLE001
            错误.append(e)

    def 只读():
        try:
            闸.wait()
            for _ in range(10):
                断档.列处置("户乙", "2026-11-22")
        except Exception as e:  # noqa: BLE001
            错误.append(e)

    ts = [threading.Thread(target=全局抽), threading.Thread(target=立桥), threading.Thread(target=只读)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
        assert not t.is_alive()
    assert not 错误, 错误

    conn = 连接()
    try:
        毒 = conn.execute(
            "SELECT COUNT(*) AS c FROM 周期快照 WHERE 状态='有效' AND 开端来源='以零为开端'"
        ).fetchone()["c"]
        assert 毒 == 0
        并存 = conn.execute(
            "SELECT COUNT(*) AS c FROM 断档桥接凭证 b "
            "JOIN 周期快照 s ON s.账户键=b.账户键 AND s.营业日=b.目标营业日 AND s.状态='有效' "
            "WHERE b.状态='有效'"
        ).fetchone()["c"]
        assert 并存 == 0, "SQL 连接查询发现有效桥接与当日有效镜并存"
    finally:
        conn.close()
