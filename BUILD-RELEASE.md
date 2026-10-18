# CXHelper 发布构建说明

## 一键构建

```bat
REM 首次：安装构建依赖（装进项目自带的 venv，Python 3.13）
venv\Scripts\pip install -r requirements-build.txt

REM 构建 dist\CXHelper\ + 测试 + 自检 + 安装包 + release\ 目录
gui\build_exe.bat
```

还需要 Inno Setup 6（默认找 `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`，别的位置用环境变量 `ISCC=<路径>` 指定）。

`gui\build_release.py` 的参数：

| 参数 | 作用 |
| --- | --- |
| （无） | 只构建 `dist\CXHelper\`，跑测试和自检 |
| `--installer` | 再编译 `installer\output\CXHelper-Setup.exe`，并整理 `release\CXHelper-<版本>\` |
| `--test-installer` | 再编译 `CXHelperTest-Setup.exe`（另一个 AppId，用来试安装/卸载，不碰正式安装） |
| `--skip-tests` | 跳过对混淆后代码的测试（不建议） |
| `--skip-selftest` | 跳过打包后自检（不建议） |

版本号只有一处：`gui/backend.py` 的 `APP_VERSION`（界面显示、exe 版本信息、安装包版本都从它来）。

## 构建流程

1. **基准值**：在临时目录里用**原始源码**运行 `gui/release_probe.py`（AES 登录加密、视频进度 enc 签名、课程列表/任务点解析、答案归一化、CLI 读配置、字体映射表等纯函数），结果作为基准。
2. **资源打包**：`gui/web` 的 HTML/CSS/JS（rcssmin/rjsmin 压缩）、logo、`config_template.ini`、`resource/font_map_table.json`、基准值和构建信息打成 zip，生成 `gui/build/stage/gui/_bundle.py`，编译进 exe。运行时界面以内存中的单个 HTML 加载，磁盘上没有 web 文件和模板。
3. **代码混淆**：GUI 用到的全部项目 `.py`（`gui/app.py`、`backend.py`、`resources.py`、`release_probe.py`、`main.py`、`api/*.py`）用 python-minifier 处理到 `gui/build/stage/`：去掉注释和文档字符串、缩短局部变量名。模块级名字不改，因为 `backend.py` 按名字给上游模块打补丁。只打包这些副本，源码不会被改动。
4. **测试混淆后的代码**：在临时目录里对混淆副本运行上游单元测试（`tests/`）、`gui/tests_force_stop.py`（14 个停止/强制停止场景）和基准探针，结果必须与原始源码完全一致。
5. **冻结**：PyInstaller 单目录构建（`--onedir --windowed`，带版本信息）。项目代码只以字节码形式存在于 `CXHelper.exe` 内部的压缩归档里。之后删掉用不到的 ddddocr 模型（`common.onnx`、`common_det.onnx`）和 OpenCV 视频 DLL（约 105 MB）。
6. **许可证**：`LICENSE.txt`（chaoxing，GPL-3.0）和 `THIRD-PARTY-NOTICES.txt`（Python 及全部打包第三方库的许可证全文）写入构建目录。
7. **检查**：扫描构建目录，确认没有 config/cookies/缓存/日志等个人文件，除 OpenCV 自己的加载配置外没有 `.py`，也没有明文项目源码。
8. **自检**：把构建复制到临时目录运行 `CXHelper.exe --selftest`（不在 dist 里运行，免得生成配置/日志）。
9. **安装包**：ISCC 编译 `installer\CXHelper.iss`。
10. **发布目录**：安装包、许可证文件、`SHA256SUMS.txt`、`build-info.json`（版本、工具版本、自检结果、大小、哈希）、本说明和构建脚本副本放进 `release\CXHelper-<版本>\`。

不打包的内容：`README.md`、`Dockerfile`、`pyproject.toml`、`.github\`、`tests\`、`config.ini`、`cookies.txt`、`cache.json`、`chaoxing.log`、`venv\`、`site\`（网站页面素材）、`_backup_*\` 等。打包内容里没有任何 API 密钥或令牌：配置模板里题库/通知的密钥字段都是空的，用户自己填写的配置保存在安装目录的 `config.ini`，不进安装包。

## 自检

```bat
CXHelper.exe --selftest            REM 离线：界面文档、设置读写、钩子、字体表、验证码 OCR、与原始源码结果一致、版本、无散落源码、真实窗口加载
CXHelper.exe --selftest --online   REM 另外：打开超星登录页（只读，不登录）
```

退出码 0 = 全部通过，详细结果在 exe 同目录的 `selftest.json`。

## 安装包行为

- 中文向导：信息（GPL-3.0 许可证，仅供阅读，不需要“同意”）→ 选择安装位置（默认 `%LOCALAPPDATA%\Programs\CXHelper`）→ 桌面快捷方式（默认勾选）→ 安装 → 可选立即运行。开始菜单快捷方式总会创建。
- 按用户安装，不需要管理员权限。程序把设置、登录状态和日志写在安装目录里，所以安装目录必须可写，选到不可写的位置（如 `C:\Program Files`）会提示换一个；静默安装时直接以错误码 7 退出。
- 运行环境检查（只检查并提示，不会安装任何东西）：Windows 10+ x64；.NET Framework 4.7.2+；Microsoft Edge WebView2 运行时。缺少时询问是否打开微软官方下载页。VC++ 运行库随程序附带。
- 升级：与之前的一键安装版同一个 AppId，原地升级到原来的目录，保留用户数据，旧版 `_internal` 和散落的 `config_template.ini` 会被清掉。
- 卸载：删除程序文件、快捷方式和卸载信息；询问是否同时删除用户数据（默认“否”）；静默卸载时保留用户数据。

## 保护强度说明

混淆 + 字节码只能防止直接看到/复制源码，挡不住有决心的逆向（Python 字节码可以被反编译，只是注释、文档字符串和局部变量名已丢失）。本机没有 C 编译器，所以没用 Cython/Nuitka；PyArmor 试用版有限制且运行时容易被杀毒软件误报，也没用。

上游 chaoxing 是 GPL-3.0：CXHelper 包含其代码，分发时必须附带许可证（已附带 `LICENSE.txt`），并且要让拿到程序的人能获得对应源代码。`THIRD-PARTY-NOTICES.txt` 里写明了上游源码地址，GUI 部分写的是“可向发布者索取”——公开分发时需要真的能提供。
