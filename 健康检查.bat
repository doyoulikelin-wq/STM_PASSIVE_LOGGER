@echo off
REM 健康检查: 探测 Nanonis 是否能连上, 报告会用哪个端口和保存目录
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "BASE=%CD%\"
set "PY=%BASE%python_runtime\python.exe"

echo.
echo ================================================================
echo   STM Logger  -  健康检查 (probe)
echo ================================================================
echo 这一步只读取 Nanonis 的几个状态值, 不会修改任何东西.
echo 看到 "ok": true 就说明一切正常.
echo ================================================================
echo.

if not exist "%PY%" (
	echo [错误] 找不到内置 Python.
	echo 当前脚本目录:
	echo     %BASE%
	echo 这个目录旁边必须能看到:
	echo     python_runtime\python.exe
	echo.
	pause
	exit /b 1
)

"%PY%" -m stm_experimenter_agent.cli probe
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
	echo.
	echo [提示] 如果 logger 已经打开并正在记录, 健康检查失败可能是正常的:
	echo        logger 会占用一个 Nanonis TCP 连接, 健康检查是第二个客户端.
	echo        已看到 logger 窗口里 session started / signals batch 时, 以 logger 为准.
	echo        如果 logger 没有运行, 请检查 Nanonis 的 TCP Programming Interface.
)

echo.
pause
exit /b %RC%
