@echo off
echo OKX Gateway - Windows 配置脚本
echo.

:: 检查管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 请以管理员身份运行此脚本！
    echo 右键点击 -^> 以管理员身份运行
    pause
    exit /b 1
)

:: 配置hosts文件
set HOSTS_FILE=%SystemRoot%\System32\drivers\etc\hosts
set DOMAIN=okx.local

:: 检查是否已存在
findstr /C:"%DOMAIN%" "%HOSTS_FILE%" >nul 2>&1
if %errorlevel% equ 0 (
    echo %DOMAIN% 已存在于 hosts 文件中
    goto :show_info
)

:: 添加hosts条目
echo. >> "%HOSTS_FILE%"
echo # OKX Gateway >> "%HOSTS_FILE%"
echo 127.0.0.1 %DOMAIN% >> "%HOSTS_FILE%"
echo 已添加 %DOMAIN% 到 hosts 文件

:show_info
echo.
echo === 访问地址 ===
echo http://localhost:8080
echo http://okx.local:8080
echo.
echo 配置完成！
pause
