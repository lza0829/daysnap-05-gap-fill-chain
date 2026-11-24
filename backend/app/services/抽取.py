import json
import uuid
from typing import Any

from app.db import 连接, 立即事务
from app.seed import 确保结构
from app.services import 断档
from app.services.时间 import 此刻, 前一营业日, 标准化营业日


def _台参(conn) -> dict[str, str]:
    return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}


def _当日净额(conn, 账户键: str, 日: str) -> int:
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(变动额),0) AS s FROM 变动流水 "
            "WHERE 账户键=? AND 营业日=? AND 命运='已入账'",
            (账户键, 日),
        ).fetchone()["s"]
    )


def _写快照(
    conn,
    账户键: str,
    日: str,
    余额: int,
    批: str,
    来源: str,
    开端余额: int | None,
    开端来源: str,
    链内补齐: list[str],
) -> None:
    conn.execute(
        "INSERT INTO 周期快照(快照键, 账户键, 营业日, 余额, 抽取批次编号, 来源, 状态, "
        "开端余额, 开端来源, 链内补齐) "
        "VALUES (?,?,?,?,?,?,'有效',?,?,?) "
        "ON CONFLICT(快照键) DO UPDATE SET "
        "余额=excluded.余额, 抽取批次编号=excluded.抽取批次编号, 来源=excluded.来源, "
        "状态='有效', 开端余额=excluded.开端余额, 开端来源=excluded.开端来源, "
        "链内补齐=excluded.链内补齐",
        (
            f"{账户键}|{日}", 账户键, 日, 余额, 批, 来源,
            开端余额, 开端来源, json.dumps(链内补齐, ensure_ascii=False),
        ),
    )


def 抽取单户(账户键: str, 营业日: str, 批次编号: str = "") -> dict[str, Any]:
    """单账户单日的同票补齐 + 抽取原子票。

    处置（补齐物化 / 断档记录 / 案件）与当日快照在同一个 BEGIN IMMEDIATE
    事务内同成败；遇有效桥接则整票回滚并抛 :class:`断档.桥接阻断Error`，
    库中不会留下任何本票快照。
    """
    确保结构()
    日 = 标准化营业日(营业日)
    批 = 批次编号.strip() if isinstance(批次编号, str) else ""
    批 = 批 or f"抽取-{日}-{uuid.uuid4().hex[:8]}"

    with 立即事务() as conn:
        户 = conn.execute("SELECT * FROM 账户 WHERE 账户键=?", (账户键,)).fetchone()
        if 户 is None:
            raise KeyError(f"账户不存在: {账户键}")
        开户日 = 户["开户营业日"]
        if 日 < 开户日:
            return {
                "账户键": 账户键, "营业日": 日, "结果": "跳过",
                "原因": "早于开户营业日", "缺日": [], "补齐": [],
            }

        参 = _台参(conn)
        重复策略 = 参.get("重复抽取", "盖写同日快照")

        # —— 互斥复检①：有效桥接存时整户阻退（事务内读，锁保护）——
        桥 = 断档.有效桥接(conn, 账户键, 日)
        if 桥 is not None:
            raise 断档.桥接阻断Error(
                f"{账户键} {日} 存在有效桥接 {桥['凭证号']}，当日抽取整拒；"
                "桥接解除前不得写当日有效镜"
            )

        旧 = conn.execute(
            "SELECT * FROM 周期快照 WHERE 账户键=? AND 营业日=?",
            (账户键, 日),
        ).fetchone()
        if 旧 is not None and 重复策略 != "盖写同日快照":
            余额 = int(旧["余额"])
            conn.execute(
                "INSERT INTO 旁置簿(原因, 账户键, 营业日, 载荷, 时刻) VALUES (?,?,?,?,?)",
                (
                    "同日已有快照", 账户键, 日,
                    json.dumps({"余额": 余额, "批次编号": 批}, ensure_ascii=False),
                    此刻(),
                ),
            )
            return {
                "账户键": 账户键, "营业日": 日, "结果": "旁置",
                "余额": 余额, "缺日": [], "补齐": [],
            }

        # —— 开端链计算 ——
        补齐日: list[str] = []
        if 日 == 开户日:
            # 唯一合法的零开端：开户首日，开户前余额为零。
            缺日: list[str] = []
            开端 = 0
            开端来源 = "开户零"
            锚点 = None
        else:
            缺日, 锚点, 锚余额 = 断档.缺日链(conn, 账户键, 日)
            # —— 互斥复检②：补齐链任一缺口日/锚点上有有效桥接，整票回滚。
            # 否则本票会把桥接目标日同票物化成有效镜，造成桥接与有效镜并存。
            链桥 = 断档.链上有效桥接(conn, 账户键, set(缺日) | ({锚点} if 锚点 else set()))
            if 链桥 is not None:
                raise 断档.桥接阻断Error(
                    f"{账户键} {日} 的补齐链途经 {链桥['目标营业日']}，"
                    f"该日存在有效桥接 {链桥['凭证号']}；跨桥抽取整拒，"
                    "桥接解除前不得物化链上有效镜"
                )
            遗留 = 断档.遗留处置阻断(conn, 账户键, set(缺日) | {日})
            if 遗留 is not None:
                raise 断档.桥接阻断Error(
                    f"{账户键} {日} 缺口 {遗留['缺营业日']} 已有未解除处置"
                    f"「{遗留['处置']}」，抽取整拒"
                )
            if 缺日:
                # —— 口径 A 核心：同一事务内沿锚点物化整条缺失日链 ——
                上一余额 = 锚余额
                for m in 缺日:
                    m净 = _当日净额(conn, 账户键, m)
                    m余额 = 上一余额 + m净
                    if m == 缺日[0] and 锚点 is not None:
                        m开端来源 = "前日快照"
                    elif m == 缺日[0]:
                        m开端来源 = "开户零"
                    else:
                        m开端来源 = "同票补齐"
                    _写快照(
                        conn, 账户键, m, m余额, 批, "同票补齐",
                        上一余额, m开端来源, [m],
                    )
                    conn.execute(
                        "INSERT INTO 断档记录"
                        "(账户键, 缺营业日, 检出批次编号, 处置, 时刻) "
                        "VALUES (?,?,?,?,?)",
                        (账户键, m, 批, "同票补齐", 此刻()),
                    )
                    补齐日.append(m)
                    上一余额 = m余额
                开端 = 上一余额  # = D-1（最后一个补齐日）的余额
                开端来源 = "同票补齐"
            else:
                # 缺日为空：锚点即 D-1 有效快照。
                开端 = 锚余额
                开端来源 = "前日快照"

        净额 = _当日净额(conn, 账户键, 日)
        余额 = 开端 + 净额
        _写快照(conn, 账户键, 日, 余额, 批, "日终抽取", 开端, 开端来源, 补齐日)

        if 缺日:
            断档._写案件(
                conn, 账户键, 日, 缺日, "已补齐", "同票补齐",
                锚点, 报告={"补齐日": 补齐日, "开端来源": 开端来源, "余额": 余额},
            )

        return {
            "账户键": 账户键,
            "营业日": 日,
            "结果": "盖写" if 旧 is not None else "写入",
            "余额": 余额,
            "开端余额": 开端,
            "开端来源": 开端来源,
            "当日流水净额": 净额,
            "缺日": 缺日,
            "补齐": 补齐日,
            "批次编号": 批,
        }


def 日终抽取(营业日: str, 批次编号: str = "") -> dict[str, Any]:
    """按营业日为全部正常账户做抽取。

    逐账户独立立即事务：跳日场景下可同时得到「甲同票补齐成镜」与
    「乙桥接阻退无镜」两种确定结果；抽取批次行只做报告，不充当处置凭证。
    """
    确保结构()
    日 = 标准化营业日(营业日)
    批 = (批次编号 or "").strip() or f"抽取-{日}-{uuid.uuid4().hex[:8]}"
    # 调用方复用同一批次编号时不要在报告行上撞主键 500：补一个短后缀。
    _c = 连接()
    try:
        if _c.execute("SELECT 1 FROM 抽取批次 WHERE 编号=?", (批,)).fetchone():
            批 = f"{批}-{uuid.uuid4().hex[:6]}"
    finally:
        _c.close()

    conn = 连接()
    try:
        账户们 = [
            dict(r)
            for r in conn.execute(
                "SELECT 账户键 FROM 账户 WHERE 状态='正常' ORDER BY 账户键"
            )
        ]
    finally:
        conn.close()

    明细: list[dict[str, Any]] = []
    写入 = 盖写 = 旁置 = 阻断 = 跳过 = 0
    补齐日数 = 断档日数 = 0
    for 户 in 账户们:
        k = 户["账户键"]
        try:
            r = 抽取单户(k, 日, 批)
        except 断档.桥接阻断Error as e:
            阻断 += 1
            # 阻退时只读算一次缺日用于报告（不写任何处置凭证）。
            rc = 连接()
            try:
                缺, _, _ = (
                    ([], None, 0)
                    if 日 <= _开户日(rc, k)
                    else 断档.缺日链(rc, k, 日)
                )
            finally:
                rc.close()
            r = {
                "账户键": k, "营业日": 日, "结果": "阻断",
                "原因": str(e), "缺日": 缺, "补齐": [],
            }
            断档日数 += len(缺)
            明细.append(r)
            continue
        明细.append(r)
        果 = r["结果"]
        if 果 == "写入":
            写入 += 1
        elif 果 == "盖写":
            盖写 += 1
        elif 果 == "旁置":
            旁置 += 1
        elif 果 == "跳过":
            跳过 += 1
        补齐日数 += len(r.get("补齐") or [])
        断档日数 += len(r.get("缺日") or [])

    报告 = {
        "营业日": 日,
        "写入": 写入,
        "盖写": 盖写,
        "旁置": 旁置,
        "阻断": 阻断,
        "跳过": 跳过,
        "补齐": 补齐日数,
        # 断档保持旧键：本批检出的缺日总数（含已补齐与桥接阻退户）。
        "断档": 断档日数,
        "账户扫描": len(账户们),
        "明细": 明细,
    }
    conn = 连接()
    try:
        conn.execute(
            "INSERT INTO 抽取批次(编号, 营业日, 时刻, 报告) VALUES (?,?,?,?)",
            (批, 日, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return {"批次编号": 批, **报告}


def _开户日(conn, 账户键: str) -> str:
    r = conn.execute("SELECT 开户营业日 FROM 账户 WHERE 账户键=?", (账户键,)).fetchone()
    return r["开户营业日"] if r is not None else "9999-12-31"
