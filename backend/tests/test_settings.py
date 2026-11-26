import os
from backend.invoice_processing.settings import load_project_env

def test_load_root_dotenv_and_distinguish_key_types(monkeypatch, tmp_path):
    monkeypatch.delenv("INVOICE_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    (tmp_path / ".env").write_text("GROQ_API_KEY=groq-test-key\nINVOICE_API_KEY=invoice-test-key\n")
    (tmp_path / ".env.invoice").write_text("GROQ_API_KEY=old-fallback-key\n")
    load_project_env(tmp_path)
    assert os.environ["GROQ_API_KEY"] == "groq-test-key"
    assert os.environ["INVOICE_API_KEY"] == "invoice-test-key"

def test_env_invoice_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("INVOICE_API_KEY", raising=False)
    (tmp_path / ".env.invoice").write_text("INVOICE_API_KEY=legacy-local-key\n")
    load_project_env(tmp_path)
    assert os.environ["INVOICE_API_KEY"] == "legacy-local-key"

def test_existing_shell_env_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("INVOICE_API_KEY", "shell-override")
    (tmp_path / ".env").write_text("INVOICE_API_KEY=file-value\n")
    load_project_env(tmp_path)
    assert os.environ["INVOICE_API_KEY"] == "shell-override"
