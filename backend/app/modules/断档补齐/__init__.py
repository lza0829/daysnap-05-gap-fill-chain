"""断档补齐 · 已贯通。

全局口径 A「以同票补齐为准」的模块入口：非开户首日缺前日镜时，在抽取
同一事务内沿最近有效锚点物化整条缺失日链，再写当日有效镜。实现落在
``app.services.抽取.抽取单户`` 与 ``app.services.断档``。
"""

启用 = True

口径 = "同票补齐"


def 说明():
    return "断档补齐：抽取票内先物化缺失日快照再抽当日，当日有效镜开端来自补齐日"


def 缺日链(conn, 账户键: str, 营业日: str):
    from app.services.断档 import 缺日链 as _缺日链

    return _缺日链(conn, 账户键, 营业日)


def 同票补齐并抽取(账户键: str, 营业日: str, 批次编号: str = ""):
    from app.services.抽取 import 抽取单户

    return 抽取单户(账户键, 营业日, 批次编号)
