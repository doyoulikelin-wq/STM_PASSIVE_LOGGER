@echo off
REM 健康检查: 探测 Nanonis 是否能连上, 报告会用哪个端口和保存目录
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ================================================================
echo   STM Logger  -  健康检查 (probe)
echo ================================================================
echo 这一步只读取 Nanonis 的几个状态值, 不会修改任何东西.
echo 看到 "ok": true 就说明一切正常.
echo ================================================================
echo.

"%~dp0python_runtime\python.exe" -m stm_experimenter_agent.cli probe

echo.
pause
