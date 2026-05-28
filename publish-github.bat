@echo off
setlocal
chcp 65001 >nul

set REPO=YLFile-subtitle-toolbox
set USER=zhangyao1989
set REMOTE=https://github.com/%USER%/%REPO%.git

where git >nul 2>&1
if %errorlevel% neq 0 (
    echo 未找到 Git，请先安装: https://git-scm.com/download/win
    pause
    exit /b 1
)

cd /d "%~dp0"

if not exist .git (
    git init
    git add .
    git commit -m "Initial release: YLFile字幕工具箱"
)

git remote remove origin 2>nul
git remote add origin %REMOTE%
git branch -M main

echo.
echo 请先在浏览器创建空仓库:
echo   https://github.com/new
echo   仓库名: %REPO%
echo   不要勾选 README / .gitignore
echo.
pause

git push -u origin main
if %errorlevel% neq 0 (
    echo.
    echo 推送失败时，可用 GitHub Desktop 或检查是否已登录:
    echo   gh auth login
    pause
    exit /b 1
)

echo.
echo 完成: https://github.com/%USER%/%REPO%
echo 建议在 Releases 上传 dist\YLFile字幕工具箱.exe
pause
endlocal
