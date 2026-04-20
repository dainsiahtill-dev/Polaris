#!/usr/bin/env python3
"""
生成不同大小的应用图标
需要安装: pip install Pillow
"""

import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("错误: 需要安装 Pillow 库")
    print("运行: pip install Pillow")
    sys.exit(1)

def generate_icons():
    """生成不同大小的图标文件"""
    current_dir = Path(__file__).parent
    source_icon = current_dir / "Polaris-icon2.png"
    
    if not source_icon.exists():
        print(f"错误: 找不到源图标文件 {source_icon}")
        return False
    
    # 创建 icons 目录
    icons_dir = current_dir / "icons"
    icons_dir.mkdir(exist_ok=True)
    
    try:
        # 打开源图标
        with Image.open(source_icon) as img:
            print(f"源图标尺寸: {img.size}")
            
            # 生成不同尺寸的 PNG 图标
            sizes = {
                "icon-16.png": 16,
                "icon-32.png": 32, 
                "icon-48.png": 48,
                "icon-64.png": 64,
                "icon-128.png": 128,
                "icon-256.png": 256,
                "icon-512.png": 512,
            }
            
            for filename, size in sizes.items():
                resized = img.resize((size, size), Image.Resampling.LANCZOS)
                output_path = icons_dir / filename
                resized.save(output_path, "PNG")
                print(f"✓ 生成 {filename} ({size}x{size})")
            
            # 生成 ICO 文件 (Windows)
            ico_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
            ico_path = icons_dir / "icon.ico"
            img.save(ico_path, "ICO", sizes=ico_sizes)
            print("✓ 生成 icon.ico (Windows)")
            
            # 生成一个标准大小的 icon.png
            standard_icon = icons_dir / "icon.png"
            if img.size[0] >= 512:
                resized = img.resize((512, 512), Image.Resampling.LANCZOS)
            else:
                resized = img.copy()
            resized.save(standard_icon, "PNG")
            print(f"✓ 生成 icon.png ({resized.size[0]}x{resized.size[1]})")
            
            print(f"\n所有图标已生成到: {icons_dir}")
            return True
            
    except Exception as e:
        print(f"错误: 生成图标时失败 - {e}")
        return False

if __name__ == "__main__":
    success = generate_icons()
    sys.exit(0 if success else 1)
