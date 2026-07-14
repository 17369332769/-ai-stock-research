-- PostgreSQL 初始化（只在数据卷为空时执行一次，由官方镜像 entrypoint 调用）。
-- 业务表结构一律由 Alembic 迁移创建（spec §19.1：迁移在服务启动前执行），这里只做实例级设置。

-- 所有时间按 Asia/Shanghai 处理（spec §8）。表里全部是 timestamptz，
-- 这里设的是会话默认展示时区，不改变存储语义（存储始终是 UTC 瞬时）。
DO $$
BEGIN
  EXECUTE format('ALTER DATABASE %I SET timezone TO %L', current_database(), 'Asia/Shanghai');
END
$$;

-- gen_random_uuid()：documents / analyses / jobs / predictions 的主键
CREATE EXTENSION IF NOT EXISTS pgcrypto;
