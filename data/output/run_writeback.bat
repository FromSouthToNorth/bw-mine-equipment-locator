@echo off
chcp 65001 >nul
echo ========================================
echo  8385 回写脚本 - 窑街煤电金河煤矿
echo  用户: D99795450
echo ========================================
echo.

cd /d "F:\gis\Point"

echo 正在执行回写...
python data\output\writeback_8385.py D99795450 "data\output\locator_result_D99795450_窑街煤电金河煤矿.json"

echo.
if %errorlevel% equ 0 (
    echo ✅ 回写执行完毕
) else (
    echo ❌ 回写出错，请检查上方错误信息
)

pause
