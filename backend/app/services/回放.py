import json
import uuid
from typing import Any

from app.db import 连接
from app.seed import 确保结构
from app.services import 断档
from app.services.时间 import 此刻, 前一营业日, 标准化营业日


def 回放核对(营业日: str, 批次编号: str = "") -> dict[str, Any]:
    """用前日快照+当日流水重算，对照当日周期快照，写入差额/跳变。

    协同口径：前日有效镜缺失且非开户首日时，不再静默以零为开端算出一个
    假相符/假差额，而是落「断档未贯通」差额行并令本批不通过；有效桥接
    阻断导致的缺当日镜也会在原因里点名桥接。同票补齐后链条连续，回放守恒。
    """
    确保结构()
    日 = 标准化营业日(营业日)
    批 = 批次编号.strip() or f"回放-{日}-{uuid.uuid4().hex[:8]}"
    conn = 连接()
    try:
        参 = {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
        try:
            阈值 = int(参.get("跳变阈值分", "0") or "0")
        except ValueError:
            阈值 = 0
        账户们 = [dict(r) for r in conn.execute("SELECT * FROM 账户 WHERE 状态='正常'")]
        相符 = 0
        差额数 = 0
        跳变数 = 0
        无快照 = 0
        断档未通 = 0
        for 户 in 账户们:
            账户键 = 户["账户键"]
            开户日 = 户["开户营业日"]
            if 日 < 开户日:
                continue
            快照 = conn.execute(
                "SELECT * FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                (账户键, 日),
            ).fetchone()
            桥 = 断档.有效桥接(conn, 账户键, 日)
            if not 快照:
                无快照 += 1
                if 桥 is not None:
                    原因 = f"断档桥接阻断（{桥['凭证号']}），缺当日快照"
                else:
                    原因 = "缺当日快照"
                conn.execute(
                    "INSERT INTO 差额行(回放批次编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (批, 账户键, 日, None, None, 0, 原因, 此刻()),
                )
                差额数 += 1
                continue

            # 开端链复检：开户首日合法零开端；其余日期前日镜必须在位，
            # 否则视为断档未贯通，不得用零开端假算守恒。
            if 日 == 开户日:
                开端 = 0
            else:
                前 = 前一营业日(日)
                前日快照 = conn.execute(
                    "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
                    (账户键, 前),
                ).fetchone()
                if 前日快照 is None:
                    缺, _, _ = 断档.缺日链(conn, 账户键, 日)
                    断档未通 += 1
                    差额数 += 1
                    conn.execute(
                        "INSERT INTO 差额行(回放批次编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, 时刻) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (
                            批, 账户键, 日, int(快照["余额"]), None, 0,
                            f"断档未贯通：缺 {','.join(缺) or 前} 有效镜，开端不可信，禁止零开端回放",
                            此刻(),
                        ),
                    )
                    continue
                开端 = int(前日快照["余额"])

            净额 = int(
                conn.execute(
                    "SELECT COALESCE(SUM(变动额),0) AS s FROM 变动流水 "
                    "WHERE 账户键=? AND 营业日=? AND 命运='已入账'",
                    (账户键, 日),
                ).fetchone()["s"]
            )
            回放余额 = 开端 + 净额
            快照余额 = int(快照["余额"])
            差额 = 快照余额 - 回放余额
            if 差额 == 0:
                相符 += 1
            else:
                conn.execute(
                    "INSERT INTO 差额行(回放批次编号, 账户键, 营业日, 快照余额, 回放余额, 差额, 原因, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (批, 账户键, 日, 快照余额, 回放余额, 差额, "快照与回放不一致", 此刻()),
                )
                差额数 += 1
            偏离 = abs(快照余额 - 回放余额)
            if 阈值 > 0 and 偏离 >= 阈值:
                conn.execute(
                    "INSERT INTO 跳变记录(账户键, 营业日, 前日余额, 当日余额, 流水净额, 偏离, 检出批次编号, 时刻) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (账户键, 日, 开端, 快照余额, 净额, 偏离, 批, 此刻()),
                )
                跳变数 += 1
        通过 = 差额数 == 0 and 无快照 == 0 and 断档未通 == 0
        报告 = {
            "营业日": 日,
            "相符": 相符,
            "差额": 差额数,
            "跳变": 跳变数,
            "无快照": 无快照,
            "断档未通": 断档未通,
            "通过": 通过,
        }
        conn.execute(
            "INSERT INTO 回放批次(编号, 营业日, 时刻, 报告) VALUES (?,?,?,?)",
            (批, 日, 此刻(), json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
        return {"批次编号": 批, **报告}
    finally:
        conn.close()
