import time


class TestToolCallPerformance:
    def test_simple_tool_latency(self, benchmark):
        """测试简单工具调用延迟"""
        def simple_tool():
            # 模拟简单工具调用
            time.sleep(0.001)  # 1ms simulated work
            return "result"

        result = benchmark.measure(simple_tool)
        print(f"\nSimple tool latency: {result}")
        assert result['p95'] < 0.01  # 10ms budget

    def test_complex_tool_latency(self, benchmark):
        """测试复杂工具调用延迟"""
        def complex_tool():
            # 模拟复杂工具调用
            time.sleep(0.005)  # 5ms simulated work
            return {"data": "complex_result"}

        result = benchmark.measure(complex_tool)
        print(f"\nComplex tool latency: {result}")
        assert result['p95'] < 0.05  # 50ms budget

class TestContextAssemblyPerformance:
    def test_context_assembly_latency(self, benchmark):
        """测试上下文组装延迟"""
        def assemble_context():
            # 模拟上下文组装
            context = {"messages": [], "tools": []}
            for i in range(10):
                context["messages"].append({"role": "user", "content": f"msg_{i}"})
            return context

        result = benchmark.measure(assemble_context)
        print(f"\nContext assembly latency: {result}")
        assert result['p95'] < 0.1  # 100ms budget
