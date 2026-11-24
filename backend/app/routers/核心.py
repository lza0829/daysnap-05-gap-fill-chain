import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.db import 夹具目录, 连接
from app.services import 回放, 抽取, 目录, 装载, 断档

路由 = APIRouter(prefix="/api")


class 装载请求(BaseModel):
    批次编号: str = ""
    流水: list = Field(default_factory=list)


class 灵活请求(BaseModel):
    model_config = ConfigDict(extra="allow")

    营业日: str = ""
    目标营业日: str = ""
    日: str = ""
    date: str = ""
    day: str = ""
    账户键: str = ""
    账户: str = ""
    account: str = ""
    account_id: str = ""
    acct: str = ""
    凭证号: str = ""
    桥接凭证号: str = ""
    voucher: str = ""
    原因: str = ""
    reason: str = ""
    批次编号: str = ""
    动作: str = ""
    action: str = ""
    处置: str = ""


def _选(d: dict[str, Any], *键s: str) -> str:
    for k in 键s:
        v = d.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _日(体: 灵活请求) -> str:
    return _选(
        体.model_dump(),
        "营业日", "目标营业日", "日", "date", "day",
        "business_date", "target_day", "target_date",
    )


def _户(体: 灵活请求) -> str:
    return _选(体.model_dump(), "账户键", "账户", "account", "account_id", "acct")


def _号(体: 灵活请求) -> str:
    return _选(体.model_dump(), "凭证号", "桥接凭证号", "voucher")


def _由(体: 灵活请求) -> str:
    return _选(体.model_dump(), "原因", "reason")


def _http错(e: Exception) -> HTTPException:
    if isinstance(e, (断档.桥接阻断Error, 断档.状态冲突Error)):
        return HTTPException(409, str(e))
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, KeyError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(400, str(e))
    return HTTPException(500, repr(e))


@路由.get("/健康")
def 健康():
    return {
        "正常": True,
        "项目": "日镜账",
        "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
        "断档口径": "同票补齐（备选桥接阻断为显式处置，二者不混用）",
    }


@路由.get("/总览")
def 取总览():
    return 目录.总览()


@路由.get("/台参")
def 取台参():
    conn = 连接()
    try:
        return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
    finally:
        conn.close()


@路由.get("/账户")
def 列账户():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 账户 ORDER BY 账户键")]
    finally:
        conn.close()


@路由.get("/流水")
def 列流水(营业日: str = "", 账户键: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 变动流水 WHERE 1=1"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 发生时 DESC LIMIT 300"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/快照")
def 列快照(营业日: str = "", 账户键: str = "", 全部状态: bool = False):
    conn = 连接()
    try:
        sql = "SELECT * FROM 周期快照 WHERE 1=1"
        args: list = []
        if not 全部状态:
            sql += " AND 状态='有效'"
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 账户键 LIMIT 500"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/差额")
def 列差额(回放批次编号: str = ""):
    conn = 连接()
    try:
        if 回放批次编号:
            rows = conn.execute(
                "SELECT * FROM 差额行 WHERE 回放批次编号=? ORDER BY 编号 DESC",
                (回放批次编号,),
            )
        else:
            rows = conn.execute("SELECT * FROM 差额行 ORDER BY 编号 DESC LIMIT 200")
        return [dict(r) for r in rows]
    finally:
        conn.close()


@路由.get("/断档")
def 列断档():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 断档记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


# ---------------- 断档处置只读接口（缺日 / 处置 / 当日可否抽 / valid） ----------------

@路由.get("/断档处置")
@路由.get("/断档状态")
@路由.get("/gap-disposition")
def 取断档处置(
    账户键: str = "",
    营业日: str = "",
    account: str = "",
    date: str = "",
    day: str = "",
):
    户 = 账户键 or account
    日 = 营业日 or date or day
    try:
        return 断档.列处置(户.strip(), 日.strip())
    except (ValueError, KeyError) as e:
        raise _http错(e) from e


@路由.get("/断档处置/{acct}/{day}")
def 取单户断档处置(acct: str, day: str):
    # 路径参数名必须 ASCII：Starlette 路径形参正则不收中文形参名。
    try:
        return 断档.列处置(acct, day)
    except (ValueError, KeyError) as e:
        raise _http错(e) from e


@路由.get("/断档桥接")
def 列断档桥接(状态: str = ""):
    return 断档.列桥接(状态)


@路由.post("/断档桥接")
@路由.post("/桥接")
@路由.post("/断档旁置")
def 立断档桥接(体: 灵活请求):
    户, 日 = _户(体), _日(体)
    if not 户 or not 日:
        raise HTTPException(400, "立桥接须提供 账户键 与 营业日")
    try:
        return 断档.建立桥接(户, 日, _由(体))
    except (ValueError, KeyError, 断档.状态冲突Error) as e:
        raise _http错(e) from e


@路由.post("/断档桥接/解除")
@路由.post("/断档桥接解除")
@路由.post("/桥接解除")
def 解断档桥接(体: 灵活请求):
    try:
        return 断档.解除桥接(_户(体), _日(体), _号(体))
    except (ValueError, LookupError) as e:
        raise _http错(e) from e


@路由.post("/断档补齐")
@路由.post("/补齐")
def 执行断档补齐(体: 灵活请求):
    """显式同票补齐+抽取单户；与 POST /抽取 带 账户键 等价。"""
    户, 日 = _户(体), _日(体)
    if not 户 or not 日:
        raise HTTPException(400, "同票补齐须提供 账户键 与 营业日")
    try:
        return 抽取.抽取单户(户, 日, 体.批次编号)
    except (ValueError, KeyError, 断档.桥接阻断Error) as e:
        raise _http错(e) from e


@路由.post("/断档处置")
def 断档处置动作(体: 灵活请求):
    """兼容用统一处置入口：按 动作/action/处置 分派到立桥接、解除或补齐。"""
    动作 = (_选(体.model_dump(), "动作", "action", "处置方式", "处置") or "").strip()
    户, 日 = _户(体), _日(体)
    if "解除" in 动作:
        try:
            return 断档.解除桥接(户, 日, _号(体))
        except (ValueError, LookupError) as e:
            raise _http错(e) from e
    if "桥" in 动作 or "旁置" in 动作 or "阻断" in 动作:
        if not 户 or not 日:
            raise HTTPException(400, "立桥接须提供 账户键 与 营业日")
        try:
            return 断档.建立桥接(户, 日, _由(体))
        except (ValueError, KeyError, 断档.状态冲突Error) as e:
            raise _http错(e) from e
    if "补" in 动作 or "抽" in 动作:
        if not 户 or not 日:
            raise HTTPException(400, "补齐须提供 账户键 与 营业日")
        try:
            return 抽取.抽取单户(户, 日, 体.批次编号)
        except (ValueError, KeyError, 断档.桥接阻断Error) as e:
            raise _http错(e) from e
    raise HTTPException(400, f"无法识别的断档处置动作: {动作 or '(空)'}")


@路由.get("/跳变")
def 列跳变():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 跳变记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/旁置")
def 列旁置():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 旁置簿 ORDER BY 编号 DESC LIMIT 100")]
    finally:
        conn.close()


def _列批(表: str):
    conn = 连接()
    try:
        out = []
        for r in conn.execute(f"SELECT * FROM {表} ORDER BY 时刻 DESC LIMIT 40"):
            d = dict(r)
            try:
                d["报告"] = json.loads(d.get("报告") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


@路由.get("/装载批次")
def 列装载批():
    return _列批("装载批次")


@路由.get("/抽取批次")
def 列抽取批():
    return _列批("抽取批次")


@路由.get("/回放批次")
def 列回放批():
    return _列批("回放批次")


@路由.get("/夹具")
def 列夹具():
    if not 夹具目录.exists():
        return []
    return sorted(p.name for p in 夹具目录.glob("*.json"))


@路由.post("/装载")
def 执行装载(体: 装载请求):
    try:
        return 装载.装载流水(体.流水, 体.批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class 夹具请求(BaseModel):
    名: str


@路由.post("/装载夹具/{name}")
def 装载夹具路径(name: str):
    return _装载夹具文件(name)


@路由.post("/装载夹具")
def 装载夹具体(体: 夹具请求):
    return _装载夹具文件(体.名)


def _装载夹具文件(名: str):
    path = 夹具目录 / 名
    if not path.exists():
        raise HTTPException(404, f"夹具不存在: {名}")
    data = json.loads(path.read_text(encoding="utf-8"))
    流水 = data.get("流水") or data.get("事件") or []
    out = 装载.装载流水(流水, str(data.get("批次编号") or ""))
    桥接们 = data.get("桥接") or data.get("桥接凭证") or []
    桥接结果 = []
    for spec in 桥接们:
        if not isinstance(spec, dict):
            continue
        户 = str(spec.get("账户键") or spec.get("账户") or "")
        日 = str(spec.get("营业日") or spec.get("目标营业日") or "")
        try:
            桥接结果.append(断档.建立桥接(户, 日, str(spec.get("原因") or "")))
        except 断档.状态冲突Error as e:
            桥接结果.append({"账户键": 户, "营业日": 日, "冲突": str(e)})
    if 桥接结果:
        out = {**out, "预置桥接": 桥接结果}
    return out


@路由.post("/抽取")
def 执行抽取(体: 灵活请求):
    日 = _日(体)
    户 = _户(体)
    if 户:
        try:
            return 抽取.抽取单户(户, 日, 体.批次编号)
        except (ValueError, KeyError, 断档.桥接阻断Error) as e:
            raise _http错(e) from e
    try:
        return 抽取.日终抽取(日, 体.批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/回放")
def 执行回放(体: 灵活请求):
    日 = _日(体)
    try:
        return 回放.回放核对(日, 体.批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/重置业务库")
def 重置():
    """开发冒烟/测试用：清业务表并保留账户与台参。"""
    conn = 连接()
    try:
        for t in (
            "变动流水",
            "抽取批次",
            "周期快照",
            "回放批次",
            "差额行",
            "断档记录",
            "断档案件",
            "断档桥接凭证",
            "跳变记录",
            "旁置簿",
            "装载批次",
        ):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        return {"完成": True, **目录.总览()}
    finally:
        conn.close()
