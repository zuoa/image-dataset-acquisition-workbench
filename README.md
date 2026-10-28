# Vannus · 图像数据集采集工作台

基于 **PySide6 + QFluentWidgets** 的离线桌面应用。通过普通 USB UVC 相机预览、拍照与录制视频，按数据集和批次保存实验素材，使用多个矩形热区自动裁剪照片，并以 ZIP 迁移、分享和恢复。

Vannus 的图标由两片叠合的影像构成字母 V。侧栏、窗口和应用包共用 `workbench/assets/icon.svg`，打包时生成对应平台的多尺寸图标。首次启动会继承旧版设置与工作区位置。

## 启动

需要 Python 3.11–3.13，推荐 [uv](https://docs.astral.sh/uv/)。

```sh
uv sync --extra dev
uv run image-workbench
# 或指定工作区
uv run image-workbench --workspace /path/to/workspace
```

也可使用 `python -m venv .venv`、`pip install -e '.[dev]'` 后运行 `image-workbench`。

## 使用

1. 新建数据集，进入「采集工作台」，新建批次并填写默认实验信息。
2. 连接 USB 摄像头，选择设备与采集分辨率，点击「连接 / 重连」。macOS 需允许相机权限。
3. 在画面中拖出一个或多个矩形热区，拖动调整位置、右下角调整尺寸；可保存为有版本的模板。
4. 点击拍照或按 Space；支持倒计时和定时间隔采集。只有文件与元数据全部保存后才提示成功。
5. 采集页右上角切换「视频」，点击「开始录制」或按 Space 开始，再次操作停止并保存。画面显示录制时长；MP4 / H.264 视频按当前数据集和批次归档，保留实验信息。视频录制完整画面，不套用热区裁剪，不采集麦克风音轨。
6. 「素材库」在同一数据集、批次下按采集时间混排图片和视频，可用「全部 / 图片 / 视频」切换类型，共用搜索与时间筛选。双击图片查看大图，双击视频调用系统播放器；选中后显示对应操作，支持混合多选移入回收站。图片保留导入、编辑、移动和历史重新裁剪。导入时可在当前窗口选择或新建数据集和批次；已归档的数据集提供「取消归档并导入」，取消窗口不创建数据。完成后自动显示导入批次的图片。ZIP 可导出整个数据集或当前筛选结果，类型筛选同样生效。
7. 素材库右上角「继续采集」返回当前数据集及选中的批次；筛选图片或视频时进入对应采集模式，「全部」保留采集页原有模式。已归档的数据集需先取消归档。
8. 「回收站」恢复误删；「设置」完整备份工作区或恢复到新目录。采集完成音效默认开启，可在「设置 → 采集完成提示」关闭或试听，开关跨次启动保留。仅在照片或视频成功保存后播放。

macOS 使用系统原生的红、黄、绿窗口按钮，位于左上角，分别执行关闭、最小化和全屏。视频正在录制时关闭窗口会先停止并保存；保存完成后可再次关闭。

界面采用白色侧栏、蓝色主操作与粉彩圆角卡片，首页展示真实的数据集、原图、今日新增、归档数量，以及包含图片和视频的最近采集记录。默认使用浅色主题，正文 17px、辅助文字 14px、标题 28px。macOS 使用 Helvetica Neue 与苹方搭配，Windows 使用微软雅黑与 Segoe UI。采集页采用主画面与单侧设置栏，相机参数、热区管理、定时拍摄和实验信息可按需展开；数据集的低频操作位于「更多」菜单。所有图片均保存为 PNG；不缩放裁剪图，原图始终保留。相机参数按实际设备能力启用。已有图片导入不自动套用模板，可在素材库显式重新裁剪。

## 数据与恢复

工作区内含 `archive.sqlite3`、`images/`、`videos/`、`.thumbnails/` 和 `.staging/`。不要手动修改数据库或素材关联路径。每个工作区只允许一个应用实例写入。采集保存先写暂存目录，再移动并提交数据库；视频等待编码器完成封装后才归档。启动会清理未提交数据。已完成编码但归档失败的视频会保存在 `recovered-recordings/`，错误提示会给出位置。旧版工作区首次打开会自动升级，保留原有照片。

- **完整数据集 ZIP**：包含 `originals/`、`crops/`、`videos/`、`manifest.json`、`metadata.csv`、`templates.json`。保留每个处理版本，视频实验信息位于清单中；支持首页导入，自动分配新编号以避免覆盖。
- **仅裁剪图 ZIP**：用于后续处理，明确不可完整恢复。
- **完整工作区备份**：包含 SQLite 一致性快照、所有原图、处理版本和已归档视频，包括回收站；恢复到新目录，下次启动生效。

导入执行路径检查、SHA-256 校验和失败回滚。取消导出不会覆盖已有目标文件。批量导入或重新裁剪取消时，已完成条目保留。文件编码 UTF-8，CSV 带 BOM 以方便表格软件打开。清单中的时间采用 UTC。

## 开发与验证

```sh
uv run pytest
uv run ruff check workbench tests
uv run python scripts/native_smoke.py
uv run python scripts/build.py
# macOS 独立应用包的原生启动检查
uv run python scripts/check_bundle.py
```

测试图像用于验证像素坐标、原图保留、历史处理、归档完整性及错误回滚；视频存储测试验证文件字节及元数据保存、迁移，不替代真实录制验收。不使用虚拟相机、模拟器或 offscreen Qt 后端。原生 smoke 会启动真实桌面窗口并将页面截图写入 `build/screenshots/`。相机验收需要实际 USB 摄像头；测试脚本不自动开启设备。

[验收清单](docs/ACCEPTANCE.md) 列出需要在 Windows、macOS 真机完成的项目；已执行结果见 [验证记录](docs/VERIFICATION.md)。

## 打包

```sh
uv run python scripts/build.py
```

在对应目标操作系统本机打包，输出 `dist/Vannus.app`（macOS）或 `dist/Vannus/Vannus.exe`（Windows）。macOS 的相机权限说明已写入 app 的 Info.plist。对外发布仍需配置代码签名、公证，以及确定 QFluentWidgets 等依赖的授权路线。

### GitHub Actions 自动构建

将包含工作流的提交推送到 GitHub 后，推送任意 tag 即可触发 `.github/workflows/build.yml`：

```sh
git tag v0.1.0
git push origin v0.1.0
```

工作流使用 Python 3.13 和 `uv.lock` 中的固定依赖，在各目标平台的 GitHub runner 上分别运行现有打包脚本：

| 压缩包 | 适用平台 | 解压内容 |
| --- | --- | --- |
| `Vannus-macos-arm64.zip` | macOS Apple Silicon | `Vannus.app` |
| `Vannus-macos-x64.zip` | macOS Intel | `Vannus.app` |
| `Vannus-windows-x64.zip` | Windows x64 | `Vannus/`，内含 `Vannus.exe` 及运行依赖 |

压缩包保存在 Actions 运行页面的 Artifacts 中，保留 30 天。三个构建全部成功后，自动创建对应 tag 的 GitHub Release 并附上压缩包；重跑工作流会更新同名附件。使用内置 `GITHUB_TOKEN`，无需额外配置发布密钥。macOS 压缩包保留应用的执行权限及符号链接；Windows 需解压并保留整个 `Vannus/` 目录后运行。

也可在 Actions → Build desktop apps → Run workflow 手动构建分支，仅生成 Artifacts；通过 CLI 或 API 指定 tag 触发手动运行时，同样会发布 Release。自动构建不启动应用或运行相机验收；macOS 仅带 ad-hoc 签名，未配置开发者证书、公证或 Windows 代码签名，真机验收仍按上述清单执行。

首版面向个人和团队内部使用，未包含 Pro 专属组件。依赖许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
