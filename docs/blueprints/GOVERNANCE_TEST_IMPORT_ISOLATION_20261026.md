# 治理回归的导入状态隔离

状态：全仓已发现测试债务，待定位。原例test_traceability_gate.py::test_gate14_adr_store_blueprint_passes在新解释器通过，在完整治理脚本回归中因captured_tool_semantics导入失败。该模块在同一工作区实际存在；不能继续把它列成缺失产品代码，也不能删原测试或改变期望。

先复现最小前序污染与原例组合，保存sys.modules/spec/path等实际差异；根因可能为测试stub/package path清理不全，仍需证据。仅修造成污染的fixture/test helper或真实产品模块缓存隔离缺陷，恢复精确原状态，避免清空整个sys.modules或靠重跑/改顺序掩盖。

接受标准：原例单独、最小污染组合、完整相关脚本在同一解释器顺序中均通过；其他冷读取／合同／authority反例不变。无业务运行、provider、目标工程或服务操作；测试临时与日志在主仓.polaris中。共享catalog checker由Root单一集成，不借此修改governance判定或baseline。
