@echo off
rem Only processes NEW uploads in the separate synthetic demo workspace.
cd /d "%~dp0.."
set "DATABASE_URL=sqlite:///.invoice_data/sundaevale_finance_demo.db"
set "INVOICE_STORAGE_DIR=.invoice_data/finance_demo_uploads"
set "INVOICE_STORAGE_BACKEND=local"
set "SUNDAEVALE_DEMO_MODE=1"
set "INVOICE_OCR_GEOMETRY=auto"
echo SundaeVale synthetic demo background worker, separate from normal invoice records
python -m backend.invoice_processing.worker --watch
