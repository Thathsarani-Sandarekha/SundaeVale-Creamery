-- For existing PostgreSQL databases only. Back up first; run once before new workers.
-- New databases use the updated SQLAlchemy schema automatically.
BEGIN;
ALTER TABLE invoice_documents ALTER COLUMN extraction_method TYPE varchar(128);
COMMIT;
