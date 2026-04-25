# Verify the false positive: '定位' appears inside '不确定位置'
prompt = '读取 helpers.py 文件的内容。注意：不要猜测路径，如果不确定位置，先用工具查找。'
idx = prompt.find('定位')
print(f'Index of 定位: {idx}')
print(f'Context: {prompt[max(0,idx-3):idx+5]!r}')
# So '不确定位置' -> '定位' is a substring match -> FALSE POSITIVE
print()
print('The word 不确定位置 accidentally contains the marker 定位')
print('This is the root cause of the HARD GATE mutation bug.')
