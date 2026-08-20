REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO signage_owner;

GRANT USAGE ON SCHEMA public TO signage_app;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA public
  TO signage_app;
GRANT USAGE, SELECT, UPDATE
  ON ALL SEQUENCES IN SCHEMA public
  TO signage_app;

ALTER DEFAULT PRIVILEGES
  FOR ROLE signage_owner
  IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO signage_app;
ALTER DEFAULT PRIVILEGES
  FOR ROLE signage_owner
  IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO signage_app;

GRANT CONNECT ON DATABASE signage TO signage_worker;
GRANT USAGE ON SCHEMA public TO signage_worker;
DO $worker_grants$
BEGIN
  IF to_regclass('public.signage_mediaasset') IS NOT NULL THEN
    EXECUTE 'GRANT SELECT ON TABLE signage_mediaasset TO signage_worker';
    EXECUTE 'REVOKE UPDATE ON TABLE signage_mediaasset FROM signage_worker';
    EXECUTE 'GRANT UPDATE (
      status,
      processing_attempts,
      processing_token,
      processing_started_at,
      processing_lease_expires_at,
      processing_finished_at,
      normalized_file,
      sha256,
      file_size,
      mime_type,
      duration_ms,
      width,
      height,
      rejection_reason,
      updated_at
    ) ON TABLE signage_mediaasset TO signage_worker';
  END IF;
END
$worker_grants$;
