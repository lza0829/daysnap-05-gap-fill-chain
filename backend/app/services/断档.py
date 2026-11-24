"""断档处置领域服务。

全局口径（关键设计取舍，只贯彻一种）
====================================

选定 **口径 A · 以同票补齐为准**：非开户首日缺前日有效镜时，抽取事务在
同一个 ``BEGIN IMMEDIATE`` 写事务内沿最近有效锚点把整条缺失日链物化为
「来源=同票补齐」的有效镜，再写当日有效镜——当日有效镜存在，且其开端
来自本票补齐日，绝不在跨断档时以零开端落正式有效镜。

**桥接阻断凭证** 是显式处置写路径（断档旁置）：操作员/并发处置先立有效
桥接后，该「账户 × 目标营业日」的抽取整户阻退、当日无有效镜，直至解除。
桥接凭证与当日有效镜在数据库事务级互斥（立桥时已有当日有效镜 → 状态冲突；
抽取时复检到有效桥接 → 桥接阻断回滚），因此并发下也不可能出现
「有效桥接 + 零开端有效镜」并存的半截态。

只读接口 :func:`处置视图` / :func:`列处置` 给出 ``缺日 / 处置 /
当日可否抽 / valid`` 四要素。
"""

import json
import uuid
from typing import Any, Optional

from app.db import 连接, 立即事务
from app.seed import 确保结构
from app.services.时间 import 此刻, 前一营业日, 标准化营业日


class 桥接阻断Error(RuntimeError):
    """有效桥接存时抽取被整户阻退（事务已回滚，无当日有效镜）。"""


class 状态冲突Error(RuntimeError):
    """处置写路径与既有有效镜冲突，典型：已有当日有效镜仍想立阻断桥接。"""


# 旧 ``断档记录.处置`` 自由文本里若出现这些明确的桥接/旁置标记且未解除，
# 也视作阻断（兼容绕过新桥接表、直接在旧表落处置的写法）。
_阻断标记 = ("桥接阻断", "桥接拒绝", "断档桥接", "立桥接", "桥接挂起", "旁置阻断", "阻断抽取")
_解除标记 = ("解除", "已处理", "已补齐", "已贯通")


def _新凭证号(账户键: str, 日: str) -> str:
    return f"桥-{账户键}-{日}-{uuid.uuid4().hex[:8]}"


def 有效桥接(conn, 账户键: str, 目标日: str):
    return conn.execute(
        "SELECT * FROM 断档桥接凭证 "
        "WHERE 账户键=? AND 目标营业日=? AND 状态='有效' LIMIT 1",
        (账户键, 目标日),
    ).fetchone()


def 链上有效桥接(conn, 账户键: str, 日集):
    """日集（通常是「当日 ∪ 缺日 ∪ 锚点」）中任一营业日存在有效桥接即命中。

    桥接不仅阻断其目标日本身的抽取，也横断任何想沿缺口链跨过该日的
    后续抽取票——否则后票的同票补齐会把桥接日重新物化成有效镜，
    造成「有效桥接 + 当日（桥接目标日）有效镜」并存。
    """
    日集 = {d for d in (日集 or ()) if d}
    if not 日集:
        return None
    占位 = ",".join("?" for _ in 日集)
    return conn.execute(
        f"SELECT * FROM 断档桥接凭证 "  # 占位符数量由内部集合决定，无注入面
        f"WHERE 账户键=? AND 状态='有效' AND 目标营业日 IN ({占位}) LIMIT 1",
        (账户键, *sorted(日集)),
    ).fetchone()


def 遗留处置阻断(conn, 账户键: str, 缺日集合: set[str]):
    """兼容旧断档记录表：命中未解除的桥接/旁置处置即阻断。"""
    if not 缺日集合:
        return None
    rows = conn.execute(
        "SELECT 缺营业日, 处置 FROM 断档记录 WHERE 账户键=? ORDER BY 编号 DESC LIMIT 200",
        (账户键,),
    ).fetchall()
    for r in rows:
        处 = r["处置"] or ""
        if r["缺营业日"] not in 缺日集合:
            continue
        if any(t in 处 for t in _阻断标记) and not any(t in 处 for t in _解除标记):
            return r
    return None


def 缺日链(conn, 账户键: str, 日: str) -> tuple[list[str], Optional[str], int]:
    """计算抽取 ``日`` 前尚缺有效镜的连续营业日。

    返回 ``(缺日升序列表, 锚点营业日, 锚点余额)``：

    * 从前一日起逐日回溯，遇到最近一张有效快照即锚点；缺日为锚点次日至前一日。
    * 一直回溯到开户日仍无快照：以「开户日前夜、余额 0」为虚拟零锚，
      缺日为开户日至前一日（开户零开端合法，但零的是开户日的开端，
      不是非开户当日的跨断档开端）。
    * ``日 == 开户日`` 由调用方先行处理，不会进入本函数的跨断档语义。
    """
    户 = conn.execute(
        "SELECT 开户营业日 FROM 账户 WHERE 账户键=?", (账户键,)
    ).fetchone()
    开户日 = 户["开户营业日"]
    cur = 前一营业日(日)
    缺: list[str] = []
    while cur >= 开户日:
        row = conn.execute(
            "SELECT 余额 FROM 周期快照 "
            "WHERE 账户键=? AND 营业日=? AND 状态='有效'",
            (账户键, cur),
        ).fetchone()
        if row is not None:
            return list(reversed(缺)), cur, int(row["余额"])
        缺.append(cur)
        cur = 前一营业日(cur)
    return list(reversed(缺)), None, 0


def _写案件(
    conn,
    账户键: str,
    目标日: str,
    缺日: list[str],
    状态: str,
    处置口径: str,
    锚点: Optional[str],
    桥接凭证号: Optional[str] = None,
    报告: Optional[dict] = None,
) -> None:
    键 = f"{账户键}|{目标日}"
    旧 = conn.execute("SELECT 版本 FROM 断档案件 WHERE 案件键=?", (键,)).fetchone()
    if 旧 is None:
        conn.execute(
            "INSERT INTO 断档案件(案件键, 账户键, 目标营业日, 缺日清单, 锚点营业日, "
            "处置口径, 状态, 桥接凭证号, 报告, 版本, 时刻, 更新时刻) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,?,?)",
            (
                键, 账户键, 目标日, json.dumps(缺日, ensure_ascii=False), 锚点,
                处置口径, 状态, 桥接凭证号,
                json.dumps(报告 or {}, ensure_ascii=False), 此刻(), 此刻(),
            ),
        )
    else:
        conn.execute(
            "UPDATE 断档案件 SET 缺日清单=?, 锚点营业日=?, 处置口径=?, 状态=?, "
            "桥接凭证号=COALESCE(?, 桥接凭证号), 报告=?, 版本=版本+1, 更新时刻=? "
            "WHERE 案件键=?",
            (
                json.dumps(缺日, ensure_ascii=False), 锚点, 处置口径, 状态,
                桥接凭证号, json.dumps(报告 or {}, ensure_ascii=False), 此刻(), 键,
            ),
        )


def 处置视图(conn, 账户键: str, 日: str) -> dict[str, Any]:
    """只读：单个「账户 × 营业日」的断档处置状态。绝不写库。"""
    日 = 标准化营业日(日)
    户 = conn.execute("SELECT * FROM 账户 WHERE 账户键=?", (账户键,)).fetchone()
    if 户 is None:
        raise KeyError(f"账户不存在: {账户键}")
    开户日 = 户["开户营业日"]

    桥当日 = 有效桥接(conn, 账户键, 日)
    当日镜 = conn.execute(
        "SELECT 快照键, 余额, 开端来源, 开端余额, 链内补齐 "
        "FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
        (账户键, 日),
    ).fetchone()

    if 日 < 开户日:
        return {
            "账户键": 账户键,
            "营业日": 日,
            "目标营业日": 日,
            "开户营业日": 开户日,
            "缺日": [],
            "处置": "未到开户日",
            "处置说明": "目标日早于开户营业日，不参与抽取",
            "当日可否抽": False,
            "canExtract": False,
            "valid": True,
            "有效": True,
            "当日有效镜": False,
            "桥接凭证号": None,
            "口径": "同票补齐",
        }

    if 日 == 开户日:
        缺日: list[str] = []
        锚点 = None
    else:
        缺日, 锚点, _ = 缺日链(conn, 账户键, 日)

    # 阻断面覆盖整张抽取票会读到/写到的链段：当日、全部缺日、最近锚点。
    守日集 = set(缺日) | {日}
    if 锚点:
        守日集.add(锚点)
    桥 = 桥当日 or 链上有效桥接(conn, 账户键, 守日集)
    链桥日 = 桥["目标营业日"] if 桥 is not None and 桥["目标营业日"] != 日 else None
    遗留 = 遗留处置阻断(conn, 账户键, set(缺日) | {日})
    阻断 = 桥 is not None or 遗留 is not None

    if 阻断:
        处置 = "桥接阻断"
        if 链桥日:
            说明 = (
                f"补齐链 {日} 途中的 {链桥日} 存在有效桥接"
                f"（{桥['凭证号'] if 桥 is not None else ''}），"
                "跨桥抽取整拒；桥接解除前该日及后续链上日均不得落有效镜"
            )
        else:
            说明 = "存在有效桥接/旁置处置，当日抽取整拒，桥接解除前不得落当日有效镜"
        可抽 = False
    elif 当日镜 is not None and not 缺日:
        if 当日镜["开端来源"] == "同票补齐":
            处置 = "已补齐"
            说明 = "断档已在同一抽取票内补齐，当日有效镜的开端来自补齐日"
        else:
            处置 = "无断档"
            说明 = "前日有效镜连续，当日有效镜有效"
        可抽 = True
    elif 缺日:
        处置 = "待补齐"
        说明 = "缺前日镜；按同票补齐口径，抽取时事务内先补齐缺失日再写当日"
        可抽 = True
    else:
        处置 = "待抽取"
        说明 = "前日镜已在位，尚未抽取当日"
        可抽 = True

    valid = (当日镜 is not None) and not 缺日 and not 阻断
    凭证号 = 桥["凭证号"] if 桥 is not None else None
    if 凭证号 is None:
        案 = conn.execute(
            "SELECT 桥接凭证号 FROM 断档案件 WHERE 账户键=? AND 目标营业日=?",
            (账户键, 日),
        ).fetchone()
        凭证号 = 案["桥接凭证号"] if 案 is not None else None

    return {
        "账户键": 账户键,
        "营业日": 日,
        "目标营业日": 日,
        "开户营业日": 开户日,
        "缺日": 缺日,
        "处置": 处置,
        "处置说明": 说明,
        "当日可否抽": 可抽,
        "canExtract": 可抽,
        "valid": bool(valid),
        "有效": bool(valid),
        "当日有效镜": 当日镜 is not None,
        "当日余额": int(当日镜["余额"]) if 当日镜 is not None else None,
        "开端来源": 当日镜["开端来源"] if 当日镜 is not None else None,
        "桥接凭证号": 凭证号,
        "阻断桥接营业日": (桥["目标营业日"] if 桥 is not None else None),
        "桥接横断链": bool(链桥日),
        "遗留处置": (遗留["处置"] if 遗留 is not None else None),
        "锚点营业日": 锚点,
        "口径": "同票补齐",
    }


def 列处置(账户键: str = "", 营业日: str = "") -> list[dict[str, Any]] | dict[str, Any]:
    """只读处置集合。

    给了 ``营业日``：逐正常账户给当日视图（可再按账户过滤）。
    未给 ``营业日``：返回案件表与有效桥接构成的处置工作台。
    同时给了 ``账户键`` 与 ``营业日``：直接返回单个视图对象
    （对应题目点名的四字段只读接口）。
    """
    确保结构()
    conn = 连接()
    try:
        if 营业日:
            日 = 标准化营业日(营业日)
            if 账户键:
                return 处置视图(conn, 账户键, 日)
            rows = conn.execute(
                "SELECT 账户键 FROM 账户 WHERE 状态='正常' ORDER BY 账户键"
            ).fetchall()
            return [处置视图(conn, r["账户键"], 日) for r in rows]

        键集: set[tuple[str, str]] = set()
        if 账户键:
            for r in conn.execute(
                "SELECT DISTINCT 账户键, 目标营业日 AS 日 FROM 断档案件 WHERE 账户键=? "
                "UNION SELECT DISTINCT 账户键, 目标营业日 FROM 断档桥接凭证 WHERE 账户键=?",
                (账户键, 账户键),
            ):
                键集.add((r["账户键"], r["日"]))
        else:
            for r in conn.execute(
                "SELECT 账户键, 目标营业日 AS 日 FROM 断档案件 "
                "UNION SELECT 账户键, 目标营业日 FROM 断档桥接凭证"
            ):
                键集.add((r["账户键"], r["日"]))
        out = [处置视图(conn, a, d) for a, d in sorted(键集)]
        return out
    finally:
        conn.close()


def 建立桥接(账户键: str, 营业日: str, 原因: str = "") -> dict[str, Any]:
    """立「断档桥接凭证」（断档旁置写路径）。

    与当日有效镜事务级互斥：若该账户当日已有有效镜，说明同票补齐/抽取
    已先成交，再立阻断桥接会造成口径冲突，抛 :class:`状态冲突Error`。
    已存在有效桥接时幂等返回原凭证。
    """
    确保结构()
    日 = 标准化营业日(营业日)
    账户键 = (账户键 or "").strip()
    if not 账户键:
        raise ValueError("账户键不能为空")
    with 立即事务() as conn:
        户 = conn.execute("SELECT * FROM 账户 WHERE 账户键=?", (账户键,)).fetchone()
        if 户 is None:
            raise KeyError(f"账户不存在: {账户键}")
        if 日 < 户["开户营业日"]:
            raise ValueError("目标营业日早于开户营业日，无需桥接")

        既有 = 有效桥接(conn, 账户键, 日)
        if 既有 is not None:
            return {
                "凭证号": 既有["凭证号"], "账户键": 账户键, "营业日": 日,
                "状态": "有效", "幂等": True, "原因": 既有["原因"],
                "缺日": json.loads(既有["缺日清单"] or "[]"),
            }

        当日镜 = conn.execute(
            "SELECT 快照键 FROM 周期快照 "
            "WHERE 账户键=? AND 营业日=? AND 状态='有效'",
            (账户键, 日),
        ).fetchone()
        if 当日镜 is not None:
            raise 状态冲突Error(
                f"{账户键} {日} 已有当日有效镜：同票补齐/抽取已成交，"
                "不得再立阻断桥接（禁止桥接与有效镜并存）"
            )

        if 日 == 户["开户营业日"]:
            缺日: list[str] = []
            锚点 = None
        else:
            缺日, 锚点, _ = 缺日链(conn, 账户键, 日)
        号 = _新凭证号(账户键, 日)
        conn.execute(
            "INSERT INTO 断档桥接凭证(凭证号, 账户键, 目标营业日, 缺日清单, 状态, 原因, 时刻) "
            "VALUES (?,?,?,?, '有效', ?,?)",
            (号, 账户键, 日, json.dumps(缺日, ensure_ascii=False), 原因 or "", 此刻()),
        )
        _写案件(
            conn, 账户键, 日, 缺日, "桥接中", "桥接阻断", 锚点,
            桥接凭证号=号, 报告={"原因": 原因 or ""},
        )
        return {
            "凭证号": 号, "账户键": 账户键, "营业日": 日, "状态": "有效",
            "幂等": False, "原因": 原因 or "", "缺日": 缺日,
        }


def 解除桥接(账户键: str = "", 营业日: str = "", 凭证号: str = "") -> dict[str, Any]:
    """解除有效桥接；解除后 ``当日可否抽`` 恢复为 true，重抽走同票补齐链。"""
    确保结构()
    账户键 = (账户键 or "").strip()
    凭证号 = (凭证号 or "").strip()
    日 = 标准化营业日(营业日) if 营业日 else ""
    if not 凭证号 and not (账户键 and 日):
        raise ValueError("解除桥接须提供 凭证号 或 账户键+营业日")

    with 立即事务() as conn:
        if 凭证号:
            桥 = conn.execute(
                "SELECT * FROM 断档桥接凭证 WHERE 凭证号=? AND 状态='有效'",
                (凭证号,),
            ).fetchone()
        else:
            桥 = 有效桥接(conn, 账户键, 日)
        if 桥 is None:
            raise LookupError("没有可解除的有效桥接凭证")

        conn.execute(
            "UPDATE 断档桥接凭证 SET 状态='已解除', 解除时刻=? WHERE 凭证号=?",
            (此刻(), 桥["凭证号"]),
        )
        conn.execute(
            "UPDATE 断档案件 SET 状态='已解除', 版本=版本+1, 更新时刻=? "
            "WHERE 账户键=? AND 目标营业日=?",
            (此刻(), 桥["账户键"], 桥["目标营业日"]),
        )
        # 旧 断档记录 表里若有同一缺口的桥接/旁置自由文本处置，一并标记解除，
        # 避免遗留处置复检继续阻断。
        conn.execute(
            "UPDATE 断档记录 SET 处置 = 处置 || '·已解除' "
            "WHERE 账户键=? AND ("
            "  处置 LIKE '%桥接%' OR 处置 LIKE '%旁置%' OR 处置 LIKE '%阻断%'"
            ") AND 处置 NOT LIKE '%解除%'",
            (桥["账户键"],),
        )
        return {
            "凭证号": 桥["凭证号"],
            "账户键": 桥["账户键"],
            "营业日": 桥["目标营业日"],
            "状态": "已解除",
        }


def 列桥接(状态: str = "") -> list[dict[str, Any]]:
    确保结构()
    conn = 连接()
    try:
        if 状态:
            rows = conn.execute(
                "SELECT * FROM 断档桥接凭证 WHERE 状态=? ORDER BY 时刻 DESC", (状态,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM 断档桥接凭证 ORDER BY 时刻 DESC LIMIT 200"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["缺日"] = json.loads(d.pop("缺日清单") or "[]")
            except Exception:
                d["缺日"] = []
            out.append(d)
        return out
    finally:
        conn.close()
