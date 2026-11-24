from datetime import date, datetime, timedelta, timezone


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 标准化营业日(值: str) -> str:
    值 = (值 or "").strip()
    if len(值) >= 10:
        return 值[:10]
    raise ValueError("营业日格式须为 YYYY-MM-DD")


def 前一营业日(营业日: str, 步数: int = 1) -> str:
    y, m, d = map(int, 营业日.split("-"))
    return (date(y, m, d) - timedelta(days=步数)).isoformat()
