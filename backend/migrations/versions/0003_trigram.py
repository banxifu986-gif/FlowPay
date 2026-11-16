# 混合检索的关键词路：pg_trgm 扩展与分块正文 GIN 索引（specs/006 US4）。
import psycopg
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade(database: str) -> None:
    if database == "knowledge":
        driver = op.get_bind().connection.driver_connection
        if not isinstance(driver, psycopg.Connection):
            raise RuntimeError("Migrations require a PostgreSQL psycopg connection")
        # pg_trgm 是 Postgres 扩展，不是 Python/前端依赖；索引让 ILIKE 子串与
        # similarity 排序不必整表扫描。扩展只在部署时创建，运行时查询不需要超级权限。
        driver.execute("""
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
        CREATE INDEX IF NOT EXISTS chunks_content_trgm
          ON chunks USING gin (content gin_trgm_ops);
        """)


def downgrade(database: str) -> None:
    _ = database
    raise RuntimeError("Restore from backup instead of destructive downgrade")
