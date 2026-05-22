@echo off
REM ============================================================
REM  启动离线标注 UI (浏览器界面)
REM  会自动开浏览器到 http://127.0.0.1:8765
REM ============================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ================================================================
echo   STM Annotation UI
echo ================================================================
echo.
echo 浏览器会自动打开 http://127.0.0.1:8765
echo 顶部填好 "标注者" 姓名后开始标.
echo.
echo 关闭服务: 在这个黑窗口里按  Ctrl+C
echo ================================================================
echo.

"%~dp0python_runtime\python.exe" -m stm_experimenter_agent.cli annotate-serve --data-root "%~dp0data" --port 8765

echo.
pause
