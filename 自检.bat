@echo off
REM ============================================================
REM  STM Logger - 自检 (verify)
REM  作用: 确认随包附带的 Python 运行时可以正常工作
REM  使用: 第一次解压后双击一次. 之后不必再跑.
REM ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "BASE=%CD%\"
set "PY=%BASE%python_runtime\python.exe"

echo.
echo ================================================================
echo   STM Logger / Annotation Tool  -  self-check
echo ================================================================
echo.

if not exist "%PY%" (
    echo [错误] 找不到内置 Python.
    echo.
    echo 当前运行的自检脚本目录是:
    echo     %BASE%
    echo.
    echo 这个目录旁边必须能看到:
    echo     python_runtime\python.exe
    echo.
    echo 常见原因:
    echo   - 还在 zip 压缩包预览窗口里直接双击, 没有完整解压
    echo   - 只复制了 .bat 文件, 没有复制整个文件夹
    echo   - 解压路径里有中文 / 空格 / 特殊符号, 建议改到 D:\stm_logger
    echo.
    pause
    exit /b 1
)

echo [1/4] 检测内置 Python ...
"%PY%" -c "import sys; print('     Python', sys.version.split()[0], 'OK')"
if errorlevel 1 goto :fail

echo.
echo [2/4] 检测关键依赖 (numpy / matplotlib / PyYAML / nanonis-spm) ...
"%PY%" -c "import numpy, matplotlib, yaml, nanonis_spm; print('     deps OK')"
if errorlevel 1 goto :fail

echo.
echo [3/4] 检测本工具 (stm_experimenter_agent) ...
"%PY%" -c "import stm_experimenter_agent; print('     stm_experimenter_agent OK')"
if errorlevel 1 goto :fail

echo.
echo [4/4] 检测标注 UI 页面和配置文件 ...
"%PY%" -c "from stm_experimenter_agent.annotation.server import _INDEX_HTML; from stm_experimenter_agent.config import load_yaml; assert _INDEX_HTML.exists(), _INDEX_HTML; load_yaml('label_schema'); print('     annotation UI files OK')"
if errorlevel 1 goto :fail

echo.
echo ================================================================
echo   自检通过! 整套工具已就绪, 不需要再装任何东西.
echo.
echo   接下来:
echo     1. 在 Nanonis 里启用  TCP Programming Interface
echo     2. 双击  健康检查.bat       看是否能连上 Nanonis
echo     3. 双击  启动logger.bat     开始记录
echo     4. Offline labels: run the annotation UI batch file.
echo ================================================================
echo.
pause
exit /b 0

:fail
echo.
echo [错误] 自检失败. 请把上面的报错截图发给开发者.
pause
exit /b 1
