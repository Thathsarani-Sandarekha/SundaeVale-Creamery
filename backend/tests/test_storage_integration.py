from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.invoice_processing.api import create_app
from backend.invoice_processing.storage import S3Storage, LocalStorage, StorageError, MAX_PDF_BYTES, configured_storage
from backend.invoice_processing.store import InvoiceDocument
from backend.invoice_processing.worker import process_one
from test_api_worker import INVOICE, pdf_bytes


class MemoryS3:
    """Offline fake exercising the actual storage adapter and its call arguments."""
    def __init__(self):
        self.objects = {}
        self.fail_on_put = None
        self.put_count = 0
        self.last_body = None

    def put_object(self, *, Bucket, Key, Body, ContentType, ServerSideEncryption):
        self.put_count += 1
        if self.put_count == self.fail_on_put:
            raise RuntimeError('private SDK message')
        assert ContentType == 'application/pdf'
        assert ServerSideEncryption == 'AES256'
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket, Key):
        data = self.objects[(Bucket, Key)]
        self.last_body = BytesIO(data)
        return {'Body': self.last_body, 'ContentLength': len(data)}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


def test_storage_default_local(tmp_path, monkeypatch):
    monkeypatch.delenv('INVOICE_STORAGE_BACKEND', raising=False)
    assert isinstance(configured_storage(tmp_path), LocalStorage)
    monkeypatch.setenv('INVOICE_STORAGE_BACKEND', 'unknown')
    with pytest.raises(ValueError):
        configured_storage(tmp_path)


def test_s3_upload_process_preview(tmp_path):
    client = MemoryS3()
    storage = S3Storage('private-test-bucket', client=client)
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', tmp_path / 'local', 'test-secret', storage)
    headers = {'x-api-key':'test-secret'}
    content = pdf_bytes(INVOICE)
    with TestClient(app) as api:
        response = api.post('/api/v1/invoices/upload', headers=headers,
                            files={'files': ('../../supplier.pdf', content, 'application/pdf')})
        assert response.status_code == 202, response.text
        doc_id = response.json()['uploaded'][0]['id']
        key = f'invoices/originals/{doc_id}.pdf'
        assert client.objects[('private-test-bucket', key)] == content
        with app.state.invoice_store.Session() as db:
            assert db.get(InvoiceDocument, doc_id).original_path == f's3://private-test-bucket/{key}'
        assert process_one(app.state.invoice_store, mode='rules') == doc_id
        assert client.last_body.closed
        result = api.get(f'/api/v1/invoices/{doc_id}', headers=headers).json()
        assert result['extraction']['total'] == '45.00'
        assert result['status'] == 'processed'
        url = f'/api/v1/invoices/{doc_id}/pdf'
        assert api.get(url).status_code == 401
        pdf = api.get(url, headers=headers)
        assert pdf.status_code == 200 and pdf.content == content
        assert pdf.headers['cache-control'] == 'no-store'
        assert pdf.headers['content-type'] == 'application/pdf'
        assert client.last_body.closed
        client.objects.clear()
        failed = api.get(url, headers=headers)
        assert failed.status_code == 503
        assert 'private-test-bucket' not in failed.text


def test_s3_batch_rollback(tmp_path):
    client = MemoryS3()
    client.fail_on_put = 2
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', tmp_path / 'local', 'secret', S3Storage('test',client=client))
    with TestClient(app) as api:
        data = pdf_bytes(INVOICE)
        result = api.post('/api/v1/invoices/upload',headers={'x-api-key':'secret'},files=[('files',('a.pdf',data,'application/pdf')),('files',('b.pdf',data,'application/pdf'))])
        assert result.status_code == 500
        assert 'private SDK' not in result.text
        assert not client.objects
        with app.state.invoice_store.Session() as db:
            assert list(db.scalars(select(InvoiceDocument))) == []


def test_s3_reference_and_size_bounds():
    client = MemoryS3(); storage = S3Storage('test', client=client)
    doc_id = str(uuid4()); ref = storage.save(doc_id,b'%PDF-demo')
    for invalid in ['s3://other/invoices/originals/'+doc_id+'.pdf','s3://test/invoices/originals/../'+doc_id+'.pdf','s3://test/other/'+doc_id+'.pdf']:
        with pytest.raises(StorageError): storage.read(invalid)
    client.objects[('test', f'invoices/originals/{doc_id}.pdf')] = b'x'*(MAX_PDF_BYTES+1)
    with pytest.raises(StorageError): storage.read(ref)
    assert client.last_body.closed


def test_s3_temp_cleanup_on_success_and_failure():
    storage = S3Storage('test',client=MemoryS3())
    ref = storage.save(str(uuid4()), b'%PDF-demo')
    with storage.materialize(ref) as path:
        assert path.read_bytes() == b'%PDF-demo'
    assert not path.exists()
    with pytest.raises(RuntimeError):
        with storage.materialize(ref) as path:
            raise RuntimeError('parser error')
    assert not path.exists()


def test_local_path_escape(tmp_path):
    storage = LocalStorage(tmp_path / 'uploads')
    other = tmp_path / 'outside.pdf'; other.write_bytes(b'private')
    with pytest.raises(StorageError): storage.read(str(other))


def test_postgres_method_column_fits_auto_ocr():
    assert InvoiceDocument.__table__.c.extraction_method.type.length >= len('docling-ocr+geometry200-auto+groq-structured')


def test_boto_sdk_contract_offline():
    boto3 = pytest.importorskip('boto3')
    from botocore.stub import Stubber
    from botocore.response import StreamingBody
    client = boto3.client('s3',region_name='us-east-1',aws_access_key_id='test',aws_secret_access_key='test')
    ident = str(uuid4()); key = 'invoices/originals/'+ident+'.pdf'; raw = b'%PDF-test'
    storage = S3Storage('test-bucket',client=client)
    with Stubber(client) as stub:
        stub.add_response('put_object',{}, {'Bucket':'test-bucket','Key':key,'Body':raw,'ContentType':'application/pdf','ServerSideEncryption':'AES256'})
        stub.add_response('get_object',{'Body':StreamingBody(BytesIO(raw),len(raw)),'ContentLength':len(raw)},{'Bucket':'test-bucket','Key':key})
        stub.add_response('delete_object',{}, {'Bucket':'test-bucket','Key':key})
        ref = storage.save(ident,raw)
        assert storage.read(ref)==raw
        storage.delete(ref)
        stub.assert_no_pending_responses()


def test_worker_watch_polls_until_next_job(monkeypatch):
    from backend.invoice_processing import worker, settings
    import sys
    monkeypatch.setattr(sys, 'argv', ['worker', '--watch', '--poll-seconds', '2'])
    monkeypatch.setattr(settings, 'load_project_env', lambda: None)
    monkeypatch.setattr(worker, 'InvoiceStore', lambda: object())
    monkeypatch.setattr(worker, 'configure_logging', lambda: None)
    calls = []
    jobs = iter([None, 'job-1'])
    def process(store):
        try:
            result = next(jobs)
        except StopIteration:
            raise KeyboardInterrupt
        calls.append(result)
        return result
    monkeypatch.setattr(worker, 'process_one', process)
    monkeypatch.setattr(worker.time, 'sleep', lambda seconds: calls.append(seconds))
    with pytest.raises(KeyboardInterrupt):
        worker.main()
    assert calls == [None, 2.0, 'job-1']


def test_s3_missing_original_fails_job_safely(tmp_path):
    client = MemoryS3()
    app = create_app(f'sqlite:///{tmp_path / "db.sqlite"}', tmp_path / 'local', 'secret', S3Storage('test',client=client))
    with TestClient(app) as api:
        headers = {'x-api-key':'secret'}
        result = api.post('/api/v1/invoices/upload', headers=headers, files={'files':('test.pdf',pdf_bytes(INVOICE),'application/pdf')})
        ident = result.json()['uploaded'][0]['id']
        client.objects.clear()
        assert process_one(app.state.invoice_store, mode='rules') == ident
        doc = api.get(f'/api/v1/invoices/{ident}',headers=headers).json()
        assert doc['status'] == 'failed'
        assert doc['error_code'] == 'UNEXPECTED_PROCESSING_ERROR'
