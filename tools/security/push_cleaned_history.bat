@echo off
REM P0-01 Git 历史清理 — 最终推送脚本 (Windows)
REM 执行此脚本以完成历史清理流程

echo ==========================================
echo P0-01 Git 历史清理 — 最终推送
echo ==========================================
echo.

REM 步骤 1: 验证工作目录
if not exist ".git" (
    echo 错误: 当前目录不是 Git 仓库
    echo 请先 cd 到 alpha 目录
    exit /b 1
)

REM 步骤 2: 验证远程配置
echo [1/5] 验证远程配置...
git remote -v
echo.

git remote -v | findstr /C:"origin" | findstr /C:"github.com/Metroids048/alpha" >nul
if errorlevel 1 (
    echo 错误: origin 远程未正确配置
    echo 请运行: git remote add origin https://github.com/Metroids048/alpha.git
    exit /b 1
)

REM 步骤 3: 验证历史已清理
echo [2/5] 验证历史已清理...
python tools\security\verify_git_history.py
if errorlevel 1 (
    echo.
    echo 错误: 仍然检测到敏感路径
    echo 历史清理可能未成功完成
    exit /b 1
)
echo.

REM 步骤 4: 显示即将推送的提交
echo [3/5] 即将推送的提交:
git log --oneline --graph --all -10
echo.

REM 步骤 5: 最终确认
echo [4/5] 警告: 最终确认
echo.
echo 此操作将:
echo   - 重写远程仓库的完整 Git 历史 (不可逆)
echo   - 删除所有敏感路径 (4555 条路径已从本地移除)
echo   - 要求其他协作者重新 clone 仓库
echo.
echo 备份位于: ..\alpha-backup-before-filter.git
echo.
set /p confirmation="确认执行 git push --force? (输入 YES 继续): "

if not "%confirmation%"=="YES" (
    echo 操作已取消
    exit /b 0
)

REM 步骤 6: 执行推送
echo.
echo [5/5] 执行推送...
echo.

git push --force --all origin
echo.
git push --force --tags origin
echo.

echo ==========================================
echo 成功: 历史清理完成！
echo ==========================================
echo.
echo 后续操作:
echo 1. 通知协作者执行:
echo    rmdir /s /q alpha ^&^& git clone https://github.com/Metroids048/alpha.git
echo.
echo 2. 验证远程仓库:
echo    git clone --depth=1 https://github.com/Metroids048/alpha.git C:\Temp\alpha-verify
echo    cd C:\Temp\alpha-verify ^&^& python tools\security\verify_git_history.py
echo.
echo 3. 备份可安全删除 (建议保留30天):
echo    rmdir /s /q ..\alpha-backup-before-filter.git
echo.
pause
