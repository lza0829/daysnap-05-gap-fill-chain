import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

数据目录 = Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))
夹具目录 = Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))
库路径 = 数据目录 / "日镜账.db"


def 连接() -> sqlite3.Connection:
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(库路径, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA busy_timeout = 30000")
    return c


@contextmanager
def 立即事务():
    """串行化处置/抽取写事务的唯一入口。

    用 autocommit 连接 + 显式 BEGIN IMMEDIATE：进入即取库级写锁，
    并发的「立桥接」与「抽取」在此排队，后到者一定看得到先到者已提交的
    桥接凭证或有效快照，从而在事务内完成互斥复检，杜绝
    「有效桥接 + 零开端有效镜」并存。
    """
    数据目录.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(库路径, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
