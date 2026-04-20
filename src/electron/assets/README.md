# Polaris 应用图标

## 文件结构

```
electron/assets/
├── Polaris-icon.png    # 原始图标 (1024x1024)
├── icons/
│   ├── icon-16.png         # 16x16
│   ├── icon-32.png         # 32x32
│   ├── icon-48.png         # 48x48
│   ├── icon-64.png         # 64x64
│   ├── icon-128.png        # 128x128
│   ├── icon-256.png        # 256x256
│   ├── icon-512.png        # 512x512
│   ├── icon.png            # 标准图标 (512x512)
│   └── icon.ico            # Windows 多尺寸 ICO
└── generate_icons.py       # 图标生成脚本
```

## 使用说明

### 重新生成图标
如果需要重新生成不同尺寸的图标：

```bash
cd electron/assets
python generate_icons.py
```

### 在代码中使用
Electron 主进程已配置使用图标：

```javascript
icon: path.join(__dirname, 'assets', 'icons', 'icon.png')
```

## 图标规格

- **原始图标**: 1024x1024 PNG
- **标准图标**: 512x512 PNG  
- **Windows ICO**: 包含 16x16 到 256x256 多尺寸
- **其他尺寸**: 16, 32, 48, 64, 128, 256, 512 像素

## 注意事项

- 图标文件会自动在开发环境和打包环境中使用
- Windows 系统主要使用 `.ico` 文件
- macOS 和 Linux 系统使用 `.png` 文件
- 如需更换图标，替换 `Polaris-icon.png` 后重新运行生成脚本
