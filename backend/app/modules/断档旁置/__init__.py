"""断档旁置（桥接阻断） · 已贯通。

显式处置写路径：立「断档桥接凭证」后，该账户 × 目标营业日的抽取整户
阻退、当日无有效镜，直至解除。桥接凭证与当日有效镜在 BEGIN IMMEDIATE
事务内互斥复检，并发下也不会并存。实现落在 ``app.services.断档``。

注意：本路径是操作员显式旁置，不是首次遇断档的默认口径；全局默认口径
仍是 :mod:`app.modules.断档补齐` 的同票补齐链。
"""

启用 = True

口径 = "桥接阻断（显式处置，非默认）"


def 说明():
    return "断档旁置：立桥接凭证 -> 当日整拒无有效镜 -> 解除后重抽走同票补齐"


def 立桥接(账户键: str, 营业日: str, 原因: str = ""):
    from app.services.断档 import 建立桥接

    return 建立桥接(账户键, 营业日, 原因)


def 解除桥接(账户键: str = "", 营业日: str = "", 凭证号: str = ""):
    from app.services.断档 import 解除桥接 as _解除

    return _解除(账户键, 营业日, 凭证号)


def 列桥接(状态: str = ""):
    from app.services.断档 import 列桥接 as _列

    return _列(状态)
