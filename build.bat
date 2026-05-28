@echo off
setlocal

set SCRIPT=main.py
set APP_NAME=YLFile字幕工具箱
set DIST=dist
set BUILD=build
set SPEC=YLFile字幕工具箱.spec

echo ========================================
echo  %APP_NAME% - 构建脚本
echo ========================================

if not exist ffmpeg.exe (
    echo.
    echo 未找到 ffmpeg.exe，请先按 tools\README.txt 放置依赖
    goto end
)

if exist %DIST% rmdir /s /q %DIST%
if exist %BUILD% rmdir /s /q %BUILD%
if exist %SPEC% del /q %SPEC%

set FFPROBE_ARG=
if exist ffprobe.exe set FFPROBE_ARG=--add-binary "ffprobe.exe;."

echo.
echo 开始打包...
echo.

pyinstaller -F -w ^
--name "%APP_NAME%" ^
--hidden-import extract ^
--hidden-import ass ^
--hidden-import utils ^
--hidden-import config ^
--hidden-import crop ^
--hidden-import encode ^
--hidden-import encode_settings ^
--add-binary "mkvmerge.exe;." ^
--add-binary "mkvextract.exe;." ^
--add-binary "ffmpeg.exe;." ^
%FFPROBE_ARG% ^
--add-binary "opencc\opencc.exe;opencc" ^
--add-binary "opencc\plugins;opencc/plugins" ^
--add-binary "share\opencc;share/opencc" ^
--add-binary "share\opencc\jieba_dict;share/opencc/jieba_dict" ^
%SCRIPT%

if %errorlevel% neq 0 (
    echo.
    echo 打包失败
    goto end
)

echo.
echo 打包完成: %CD%\%DIST%\%APP_NAME%.exe
echo 请将 config.example.json 复制为 config.json 后使用

:end
pause
endlocal
