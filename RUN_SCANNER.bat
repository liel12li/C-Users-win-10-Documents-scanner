@echo off
title Stock Scanner
echo Installing requirements...
pip install -r requirements.txt -q
echo Starting Stock Scanner...
python app.py
pause
