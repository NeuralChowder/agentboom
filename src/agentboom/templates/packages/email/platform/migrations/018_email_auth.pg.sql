-- Raw Authentication-Results header (SPF/DKIM/DMARC evidence) at ingest.
ALTER TABLE emails ADD COLUMN auth_results TEXT;
