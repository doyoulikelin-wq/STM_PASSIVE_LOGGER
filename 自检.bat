@echo off
REM ============================================================
REM  STM Logger - 自检 (verify)
REM  作用: 确认随包附带的 Python 运行时可以正常工作
REM  使用: 第一次解压后双击一次. 之后不必再跑.
REM ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ================================================================
echo   STM Logger / Annotation Tool  -  self-check
echo ================================================================
echo.

if not exist "%~dp0python_runtime\python.exe" (
    echo [错误] 找不到内置 Python:
    echo     %~dp0python_runtime\python.exe
    echo.
    echo 可能原因:
    echo   - zip 没有完整解压 (尤其不要把脚本拖出 zip 单独运行)
    echo   - 解压路径里有中文 / 空格 / 特殊符号 (请改放到纯英文路径)
    echo.
    pause
    exit /b 1
)

echo [1/3] 检测内置 Python ...
"%~dp0python_runtime\python.exe" -c "import sys; print('     Python', sys.version.split()[0], 'OK')"
if errorlevel 1 goto :fail

echo.
echo [2/3] 检测关键依赖 (numpy / matplotlib / PyYAML / nanonis-spm) ...
"%~dp0python_runtime\python.exe" -c "import numpy, matplotlib, yaml, nanonis_spm; print('     deps OK')"
if errorlevel 1 goto :fail

echo.
echo [3/3] 检测本工具 (stm_experimenter_agent) ...
"%~dp0python_runtime\python.exe" -c "import stm_experimenter_agent; print('     stm_experimenter_agent OK')"
if errorlevel 1 goto :fail

echo.
echo ================================================================
echo   自检通过! 整套工具已就绪, 不需要再装任何东西.
echo.
echo   接下来:
echo     1) 在 Nanonis 里启用  TCP Programming Interface
echo     2) 双击  健康检查.bat       看是否能连上 Nanonis
echo     3) 双击  启动logger.bat     开始记录
echo     4) 双击  打开标注UI.bat     离线对已有数据做标注
echo ================================================================
echo.
pause
exit /b 0

:fail
echo.
echo [错误] 自检失败. 请把上面的报错截图发给开发者.
pause
exit /b 1
