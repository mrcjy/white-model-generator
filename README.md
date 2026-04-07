# 3D Tiles 白膜生成器

将建筑轮廓 Shapefile 转换为 3D Tiles 1.1 格式白膜的桌面应用，支持多级 LOD 四叉树切分，可直接加载到 Cesium 等三维平台。

## 下载

前往 [Releases](https://github.com/mrcjy/white-model-generator/releases/latest) 页面，下载最新版本的 `3D Tiles 白膜生成器 Setup x.x.x.exe`，安装后即可使用。

> 仅支持 Windows x64，无需安装 Python 或其他依赖。

## 功能

- 选择建筑轮廓 `.shp` 文件，自动读取字段列表
- 选择高度属性字段，支持自动匹配常见命名（Height、高度等）
- 生成带三级 LOD 的 3D Tiles 1.1 白膜：
  - LOD 0（远景）：合并包围盒
  - LOD 1（中景）：简化轮廓拉伸
  - LOD 2（近景）：原始轮廓全精度拉伸
- 实时进度条、速率、预计剩余时间显示
- 输出标准 `tileset.json` + `tiles/` 目录结构

## 使用

安装完成后直接打开应用：

1. 点击 **选择文件** 选择建筑轮廓 `.shp` 文件
2. 在下拉框中选择高度属性字段
3. 点击 **选择目录** 选择输出位置
4. 按需调整默认高度和海拔偏移
5. 点击 **生成 3D Tiles**

生成完成后，输出目录内的 `tileset.json` 即可加载到 Cesium / CesiumJS / Cesium for Unreal 等平台。

### 参数说明

| 参数 | 说明 |
|------|------|
| 高度属性字段 | SHP 中存储建筑高度的字段（单位：米） |
| 默认高度 | 当高度字段值为空时使用的备用高度（默认 10m） |
| 海拔偏移 | 整体抬高白膜，避免建筑陷入地形（默认 15m，山区可适当调大） |

## 开发

### 环境要求

- Node.js 18+
- [uv](https://docs.astral.sh/uv/)（Python 包管理器，用于运行 Python 脚本）

### 安装依赖

```bash
npm install
```

### 启动开发模式

```bash
npm start
```

### 打包为 Windows 安装包

**第一步**：将 Python 脚本编译为独立 exe（仅需执行一次，或脚本更新后重新执行）

```powershell
npm run build:python
```

> 首次运行会自动创建 `.venv-build` 虚拟环境并安装所有依赖，耗时约 2-5 分钟。

**第二步**：打包 Electron 应用

```powershell
npm run dist
```

> 打包前需开启 Windows **开发者模式**（设置 → 系统 → 开发者选项），或以管理员身份运行终端，否则会因符号链接权限问题失败。

输出文件位于 `release/` 目录。

也可以两步合一：

```powershell
npm run build:win
```

## 项目结构

```
├── gen_3dtiles_lod.py   # Python 核心脚本（多级 LOD 四叉树生成）
├── main.js              # Electron 主进程
├── preload.js           # IPC 桥接（contextBridge）
├── index.html           # 界面
├── renderer.js          # 界面交互逻辑
├── build-python.ps1     # PyInstaller 编译脚本
└── release/             # 打包输出（不进入版本控制）
```

## 输入数据要求

- 格式：ESRI Shapefile（`.shp` + `.dbf` + `.shx`）
- 几何类型：Polygon / MultiPolygon（建筑轮廓面）
- 坐标系：任意，程序会自动转换为 WGS84（EPSG:4326）
- 高度字段：数值型，单位为米

## 依赖

| 类型 | 依赖 |
|------|------|
| 界面 | Electron |
| 打包 | electron-builder |
| GIS 读取 | geopandas、pyogrio |
| 几何处理 | shapely |
| 3D 模型写入 | pygltflib |
| 坐标转换 | pyproj |
