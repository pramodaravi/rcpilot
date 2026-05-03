@echo off
set LOG=%TEMP%\rcpilot_sync_status.log
cd /d C:\Users\Promo\dev\rcpilot
echo --- git fetch + status --- > "%LOG%" 2>&1
git fetch origin >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo --- recent commits on main --- >> "%LOG%" 2>&1
git --no-pager log --oneline -25 main >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo --- last 5 commits with stats --- >> "%LOG%" 2>&1
git --no-pager log --stat -5 >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo --- behind/ahead status --- >> "%LOG%" 2>&1
git status -sb >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo --- pulling latest --- >> "%LOG%" 2>&1
git pull --ff-only >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo --- working tree --- >> "%LOG%" 2>&1
git status --short >> "%LOG%" 2>&1
exit
