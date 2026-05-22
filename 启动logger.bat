@echo off
REM ============================================================
REM  启动一次 STM 被动采集 session
REM  会询问几个必要信息, 然后开始记录, 直到你按 Ctrl+C 退出
REM ============================================================
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ================================================================
echo   STM Logger  -  开始一次实验记录
echo ================================================================
echo.
echo 开始之前, 请先确认:
echo   1) Nanonis 主程序已经启动并打开 TCP Programming Interface
echo   2) Nanonis 的 File / Session 保存路径已经设置好
echo.

set /p OPERATOR=请输入你的姓名 (operator):
if "!OPERATOR!"=="" (
    echo [错误] 必须填姓名.
    pause
    exit /b 1
)

set /p SAMPLE=请输入样品编号 (sample, 如 BP5-001):
if "!SAMPLE!"=="" (
    echo [错误] 必须填样品编号.
    pause
    exit /b 1
)

set /p TIP=请输入针尖编号 (tip, 如 tip01):
if "!TIP!"=="" set TIP=unknown

set /p MATERIAL=请输入材料 (material, 如 Bi2Se3, 不填回车跳过):

set /p NOTES=备注 (任意, 不填回车跳过):

REM 构造命令
set ARGS=--operator "!OPERATOR!" --sample "!SAMPLE!" --tip "!TIP!"
if not "!MATERIAL!"=="" set ARGS=!ARGS! --material "!MATERIAL!"
if not "!NOTES!"=="" set ARGS=!ARGS! --notes "!NOTES!"

REM 数据落到当前目录下的 data 文件夹
set ARGS=!ARGS! --data-root "%~dp0data"

echo.
echo ================================================================
echo   即将启动 logger. 看到 "session started" 就说明在记录了.
echo   要结束: 在这个黑窗口里按  Ctrl+C  一次, 然后等它写完文件.
echo ================================================================
echo.

"%~dp0python_runtime\python.exe" -m stm_experimenter_agent.cli start !ARGS!

echo.
echo ================================================================
echo   session 已结束. 数据保存在  %~dp0data
echo ================================================================
pause
