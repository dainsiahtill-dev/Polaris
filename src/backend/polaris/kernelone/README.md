# kernelone

Polaris 技术内核层，只承载纯技术运行时能力，不吸收业务语义。

- `storage/`: 逻辑路径与存储布局真相（storage layout）
- `fs/`: 文件系统读写边界（统一 UTF-8 文本 I/O 契约）
- `db/`: 数据库统一边界（SQLite/SQLAlchemy/LanceDB 连接策略与路径治理）
- `llm/`: LLM 运行时统一入口（角色绑定、provider 归一、调用治理）
