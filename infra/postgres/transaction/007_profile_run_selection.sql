CREATE TABLE profile_group_selections (
 id UUID PRIMARY KEY,
 request_key TEXT NOT NULL UNIQUE,
 request_hash CHAR(64) NOT NULL,
 parameters JSONB NOT NULL,
 groups JSONB NOT NULL CHECK(jsonb_typeof(groups)='array'),
 group_count INTEGER NOT NULL CHECK(group_count BETWEEN 0 AND 10000),
 digest CHAR(64) NOT NULL,
 captured_at TIMESTAMPTZ NOT NULL
);
