# db

KernelOne 的数据库技术边界，负责：

- 统一 SQLite/SQLAlchemy/LanceDB 连接入口
- 路径策略与存储布局约束
- 连接错误归一化

业务仓储、业务查询语义不放在此层。

