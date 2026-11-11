@echo off
chcp 936 >nul
rem ****************************************************************
rem  Hookshot Builder 预更新面板 - Windows 启动器
rem  编码约定: GBK(936) / 无 BOM / CRLF > 本文本经 Python 以 gbk 二进制写出
rem  本脚本不启动游戏、不点 Update、不改任何游戏文件
rem ****************************************************************
setlocal
rem 脚本全部 open() 已显式指定 encoding, 故关闭 UTF-8 模式并把控制台输出锁回 GBK
set "PYTHONUTF8=0"
set "PYTHONIOENCODING=gbk"
title Hookshot Builder 预更新面板

rem 面板目录 = 本 bat 所在目录(相对定位, 不写死盘符/用户名)
set "PANEL=%~dp0"

rem 优先级: --selftest 透传; 其余参数原样交给 server.py
set "ARGS=%*"

rem 1) 优先系统 Python
set "PY="
where python >nul 2>&1
if %errorlevel%==0 set "PY=python"
if defined PY goto :havepy

rem 2) 回退: 环境变量 HOOKSHOT_BUILDER_DIR 指向的数据根里, Builder 自带 tools\python\python.exe
if not "%HOOKSHOT_BUILDER_DIR%"=="" (
  for /f "usebackq tokens=* delims=" %%v in ("%HOOKSHOT_BUILDER_DIR%\versions\current.txt") do set "TAG=%%v"
  if defined TAG (
    set "BPY=%HOOKSHOT_BUILDER_DIR%\versions\%TAG%\HookshotBuilder\tools\python\python.exe"
    if exist "%BPY%" set "PY=%BPY%"
  )
)
if defined PY goto :havepy

rem 3) 回退: 默认 %LOCALAPPDATA%\HookshotBuilder 数据根里的自带 Python
set "DEFBASE=%LOCALAPPDATA%\HookshotBuilder"
if exist "%DEFBASE%\versions\current.txt" (
  for /f "usebackq tokens=* delims=" %%v in ("%DEFBASE%\versions\current.txt") do set "DTAG=%%v"
  if defined DTAG (
    set "DBPY=%DEFBASE%\versions\%DTAG%\HookshotBuilder\tools\python\python.exe"
    if exist "%DBPY%" set "PY=%DBPY%"
  )
)

:havepy
if not defined PY (
  echo 找不到 Python。请安装 Python 3 并加入 PATH，或用 HOOKSHOT_BUILDER_DIR 指向 Builder 数据根。
  echo 面板目录: %PANEL%
  pause
  exit /b 1
)

echo Python : %PY%
echo 面板   : %PANEL%
echo.
cd /d "%PANEL%"
"%PY%" server.py %ARGS%
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo.
  echo server.py 退出码 %RC%。若报「未探测到 Builder 数据目录」，请见同目录 README.md 的路径配置一节。
  pause
)
exit /b %RC%
