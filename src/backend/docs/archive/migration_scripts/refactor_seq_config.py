"""Script to refactor _get_sequential_config"""

with open("polaris/cells/roles/adapters/internal/director_adapter.py", encoding="utf-8") as f:
    src = f.read()

# Find the _get_sequential_config method
idx = src.find("def _get_sequential_config")
end_idx = src.find("\n    async def ", idx + 1)
if end_idx == -1:
    end_idx = src.find("\n    @staticmethod", idx + 1)
if end_idx == -1:
    end_idx = idx + 5000

method_body = src[idx:end_idx]
print(f"Method body length: {len(method_body)} lines")
print(f"Uses _seq_resolve_bool: {'_seq_resolve_bool' in method_body}")
print(f"Has nested _setting_value: {'def _setting_value' in method_body}")
print(f"Has nested _resolve_bool: {'def _resolve_bool' in method_body}")
print(f"Has nested _resolve_int: {'def _resolve_int' in method_body}")
print(f"Has nested _resolve_str: {'def _resolve_str' in method_body}")
