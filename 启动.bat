@echo off
rem 启动帝王花桌面挂件。可以追加参数，例如：启动.bat --count 5 --size 400
cd /d "%~dp0"

rem 依次尝试几种常见的 Python 入口，pyw / pythonw 不会弹黑框
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 console.py %*
    goto :eof
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw console.py %*
    goto :eof
)
where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3 console.py %*
    goto :eof
)
where python >nul 2>nul
if %errorlevel%==0 (
    start "" python console.py %*
    goto :eof
)

echo 没有找到 Python。请先安装 Python 3.8 或更高版本，
echo 并执行：pip install Pillow
pause
