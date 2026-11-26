@echo off
rem Local, isolated synthetic demo only. Run after: python -m scripts.seed_finance_demo
cd /d "%~dp0.."
set "DATABASE_URL=sqlite:///.invoice_data/sundaevale_finance_demo.db"
set "INVOICE_STORAGE_DIR=.invoice_data/finance_demo_uploads"
set "INVOICE_STORAGE_BACKEND=local"
set "SUNDAEVALE_DEMO_MODE=1"
echo SundaeVale synthetic finance demo API on http://127.0.0.1:8001
python -m uvicorn backend.invoice_processing.server:app --reload --host 127.0.0.1 --port 8001
