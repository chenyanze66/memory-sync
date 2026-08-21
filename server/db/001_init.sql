CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

CREATE TYPE device_status AS ENUM ('active', 'revoked');
CREATE TYPE document_status AS ENUM ('normal', 'conflicted', 'deleted');
CREATE TYPE operation_result AS ENUM ('accepted', 'noop', 'conflict');
CREATE TYPE sync_event_type AS ENUM ('accepted', 'conflict', 'resolved');
CREATE TYPE space_kind AS ENUM ('user', 'project');
CREATE TYPE data_classification AS ENUM ('P0', 'P1', 'P2');

CREATE TABLE auth_accounts (
    id uuid PRIMARY KEY,
    email citext NOT NULL UNIQUE,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    disabled_at timestamptz
);

CREATE TABLE refresh_tokens (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    token_hash char(64) NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX refresh_tokens_user_active_idx ON refresh_tokens(user_id, expires_at) WHERE revoked_at IS NULL;

CREATE TABLE profiles (
    user_id uuid PRIMARY KEY REFERENCES auth_accounts(id) ON DELETE CASCADE,
    display_name text NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE devices (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    platform text NOT NULL CHECK (platform IN ('windows','macos','linux','cloud-agent')),
    public_key bytea NOT NULL CHECK (octet_length(public_key)=32),
    status device_status NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz,
    revoked_at timestamptz,
    UNIQUE(user_id, public_key)
);

CREATE TABLE spaces (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    slug text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]*$'),
    kind space_kind NOT NULL,
    classification data_classification NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(user_id, slug)
);

CREATE TABLE documents (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    space_id uuid NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    path citext NOT NULL CHECK (char_length(path) BETWEEN 1 AND 1024),
    head_version_id uuid,
    status document_status NOT NULL DEFAULT 'normal',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(space_id, path),
    UNIQUE(id, user_id)
);

CREATE TABLE document_versions (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL,
    user_id uuid NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    parent_version_ids uuid[] NOT NULL DEFAULT '{}',
    content_hash char(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    content text NOT NULL,
    deleted boolean NOT NULL DEFAULT false,
    author_device_id uuid NOT NULL REFERENCES devices(id),
    client_modified_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(document_id,user_id) REFERENCES documents(id,user_id) ON DELETE CASCADE
);
ALTER TABLE documents
    ADD CONSTRAINT documents_head_version_fk FOREIGN KEY(head_version_id) REFERENCES document_versions(id);
CREATE INDEX document_versions_document_created_idx ON document_versions(document_id, created_at);
CREATE INDEX document_versions_parent_ids_gin ON document_versions USING gin(parent_version_ids);

CREATE TABLE operations (
    operation_id uuid NOT NULL,
    device_id uuid NOT NULL REFERENCES devices(id),
    user_id uuid NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    result_type operation_result NOT NULL,
    result_version_id uuid REFERENCES document_versions(id),
    result_payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(device_id, operation_id)
);

CREATE TABLE sync_events (
    seq bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    space_id uuid NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_id uuid NOT NULL REFERENCES document_versions(id),
    event_type sync_event_type NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX sync_events_user_space_seq_idx ON sync_events(user_id, space_id, seq);

CREATE TABLE device_nonces (
    device_id uuid NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES auth_accounts(id) ON DELETE CASCADE,
    nonce uuid NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY(device_id, nonce)
);
CREATE INDEX device_nonces_expiry_idx ON device_nonces(expires_at);

CREATE FUNCTION app_user_id() RETURNS uuid
LANGUAGE sql STABLE PARALLEL SAFE
AS $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'profiles','devices','spaces','documents','document_versions',
        'operations','sync_events','device_nonces'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (user_id=app_user_id()) WITH CHECK (user_id=app_user_id())',
            table_name
        );
    END LOOP;
END $$;

CREATE FUNCTION reject_version_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'document versions are immutable';
END $$;

CREATE TRIGGER document_versions_immutable
BEFORE UPDATE OR DELETE ON document_versions
FOR EACH ROW EXECUTE FUNCTION reject_version_mutation();

REVOKE UPDATE, DELETE ON document_versions FROM PUBLIC;

CREATE FUNCTION cleanup_expired_security_rows() RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    DELETE FROM device_nonces WHERE expires_at < now();
    DELETE FROM refresh_tokens WHERE expires_at < now() OR revoked_at < now() - interval '7 days';
$$;
REVOKE ALL ON FUNCTION cleanup_expired_security_rows() FROM PUBLIC;
