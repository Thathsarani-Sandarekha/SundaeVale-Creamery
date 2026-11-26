"""Bounded original-PDF storage. Local by default; optional private S3 objects."""
from __future__ import annotations
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

MAX_PDF_BYTES = 8 * 1024 * 1024


class StorageError(RuntimeError):
    """Storage failures; never expose SDK messages or invoice contents."""


def object_name(document_id: str) -> str:
    return f"{UUID(document_id)}.pdf"


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, reference: str) -> Path:
        path = Path(reference).resolve()
        if not path.is_relative_to(self.root) or path.suffix.lower() != '.pdf':
            raise StorageError('INVALID_STORAGE_REFERENCE')
        return path

    def save(self, document_id: str, data: bytes) -> str:
        if len(data) > MAX_PDF_BYTES:
            raise StorageError('PDF_TOO_LARGE')
        target = self.root / object_name(document_id)
        with target.open('xb') as f:
            try:
                f.write(data)
            except OSError:
                f.close()
                target.unlink(missing_ok=True)
                raise
        return str(target)

    def delete(self, reference: str):
        self._path(reference).unlink(missing_ok=True)

    def read(self, reference: str) -> bytes:
        with self._path(reference).open('rb') as f:
            data = f.read(MAX_PDF_BYTES + 1)
        if len(data) > MAX_PDF_BYTES:
            raise StorageError('PDF_TOO_LARGE')
        return data

    @contextmanager
    def materialize(self, reference: str):
        path = self._path(reference)
        if path.stat().st_size > MAX_PDF_BYTES:
            raise StorageError('PDF_TOO_LARGE')
        yield path


class S3Storage:
    def __init__(self, bucket: str, prefix: str = 'invoices/originals', client=None):
        self.bucket = bucket.strip()
        self.prefix = prefix.strip('/')
        if not self.bucket or not self.prefix or any(p in ('', '.', '..') for p in self.prefix.split('/')):
            raise ValueError('Configure a private S3 bucket and a valid object prefix')
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client('s3', config=Config(connect_timeout=5, read_timeout=30,
                                                       retries={'mode': 'standard', 'total_max_attempts': 3}))
        self.client = client

    def _key(self, reference: str) -> str:
        base = f's3://{self.bucket}/{self.prefix}/'
        if not reference.startswith(base):
            raise StorageError('INVALID_STORAGE_REFERENCE')
        name = reference[len(base):]
        try:
            if not name.endswith('.pdf') or object_name(name[:-4]) != name:
                raise ValueError('bad object name')
        except ValueError:
            raise StorageError('INVALID_STORAGE_REFERENCE') from None
        return f'{self.prefix}/{name}'

    def save(self, document_id: str, data: bytes) -> str:
        if len(data) > MAX_PDF_BYTES:
            raise StorageError('PDF_TOO_LARGE')
        key = f'{self.prefix}/{object_name(document_id)}'
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType='application/pdf', ServerSideEncryption='AES256')
        return f's3://{self.bucket}/{key}'

    def delete(self, reference: str):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(reference))

    def read(self, reference: str) -> bytes:
        result = self.client.get_object(Bucket=self.bucket, Key=self._key(reference))
        body = result['Body']
        try:
            if result.get('ContentLength', 0) > MAX_PDF_BYTES:
                raise StorageError('PDF_TOO_LARGE')
            data = body.read(MAX_PDF_BYTES + 1)
            if len(data) > MAX_PDF_BYTES:
                raise StorageError('PDF_TOO_LARGE')
            return data
        finally:
            body.close()

    @contextmanager
    def materialize(self, reference: str):
        with TemporaryDirectory(prefix='sundaevale-') as directory:
            path = Path(directory) / 'invoice.pdf'
            path.write_bytes(self.read(reference))
            yield path


def configured_storage(root: Path):
    mode = os.getenv('INVOICE_STORAGE_BACKEND', 'local').strip().lower()
    if mode == 'local':
        return LocalStorage(root)
    if mode == 's3':
        return S3Storage(os.getenv('INVOICE_S3_BUCKET', ''),
                         os.getenv('INVOICE_S3_PREFIX', 'invoices/originals'))
    raise ValueError('INVOICE_STORAGE_BACKEND must be local or s3')
