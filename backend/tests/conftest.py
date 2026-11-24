# -*- coding: utf-8 -*-
"""本目录测试的隔离引导：在导入 app.db 前把数据目录指到临时目录。"""
import os
import pathlib
import sys
import tempfile

import pytest

本目录 = pathlib.Path(__file__).resolve().parent
后端目录 = 本目录.parent
仓库根 = 后端目录.parent
sys.path.insert(0, str(后端目录))

_临时数据 = pathlib.Path(tempfile.mkdtemp(prefix="daysnap-pytest-"))
os.environ["数据目录"] = str(_临时数据)
os.environ.setdefault("夹具目录", str(仓库根 / "fixtures"))

import app.db as db  # noqa: E402

# 即便 app.db 已被外层 conftest 提前导入，也强制把库路径钉到临时库。
db.数据目录 = _临时数据
db.库路径 = _临时数据 / "test-日镜账.db"

import app.seed as seed  # noqa: E402

seed.初始化库()

_业务表 = (
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
)


def 清库() -> None:
    conn = db.连接()
    try:
        for t in _业务表:
            conn.execute(f"DELETE FROM {t}")
        # 并发用例自建的竞户一并清掉，保留甲/乙/丙种子户与台参。
        conn.execute("DELETE FROM 账户 WHERE 账户键 LIKE '竞户%'")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _每例干净库():
    清库()
    yield
    清库()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
