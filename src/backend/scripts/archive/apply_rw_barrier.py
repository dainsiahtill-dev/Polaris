from pathlib import Path

pt_path = Path('c:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py')
content = pt_path.read_text(encoding='utf-8')

new_logic = '''        # --- Read-Write Barrier ---
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry
        has_read = False
        has_write = False
        read_tools = []
        write_tools = []
        for inv in invocations:
            tname = extract_invocation_tool_name(inv)
            if not tname: continue
            spec = ToolSpecRegistry.get(tname)
            if spec:
                if spec.is_read_tool():
                    has_read = True
                    read_tools.append(tname)
                if spec.is_write_tool():
                    has_write = True
                    write_tools.append(tname)
        if has_read and has_write:
            overlap = set(read_tools) & set(write_tools)
            if not overlap:
                raise RuntimeError(
                    f"single_batch_contract_violation: Cannot mix Read tools ({','.join(set(read_tools))}) "
                    f"and Write tools ({','.join(set(write_tools))}) in the same parallel batch. "
                    "You must wait for read results to return before writing."
                )
'''

# Find insertion point
insertion_point = "        latest_user_request = extract_latest_user_message(context)"
if "Read-Write Barrier" not in content and insertion_point in content:
    content = content.replace(insertion_point, new_logic + '\n' + insertion_point)
    pt_path.write_text(content, encoding='utf-8')
    print("Read-Write Barrier successfully injected into tool_batch_executor.py")
else:
    print("Read-Write Barrier already present or insertion point not found.")
