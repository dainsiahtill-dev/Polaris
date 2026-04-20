@echo off
cd /d "C:\Users\dains\Documents\GitLab\polaris\src\backend"
python -m pytest polaris/kernelone/events/tests/test_message_bus.py -v --tb=short -x
