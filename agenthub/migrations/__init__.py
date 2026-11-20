"""数据库迁移。

约定：
- 每个迁移一个模块，暴露 ``VERSION`` / ``DESCRIPTION`` / ``up(conn)``
- 迁移必须幂等（先检查列/索引是否存在），因为新库由 ``create_all`` 直接建出目标结构
- 版本号只增不改，已应用的迁移不再重复执行
"""
from .runner import MIGRATIONS, applied_versions, run_migrations

__all__ = ["MIGRATIONS", "applied_versions", "run_migrations"]
