@echo off
REM 打开数据文件夹
chcp 65001 >nul
cd /d "%~dp0"
if not exist "data" mkdir "data"
explorer "%~dp0data"
