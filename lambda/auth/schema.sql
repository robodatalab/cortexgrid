-- RoboDataLab investor auth schema
-- Run once against the RDS instance after provisioning.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS investor_access (
  id             SERIAL PRIMARY KEY,
  email          VARCHAR(255) UNIQUE NOT NULL,
  status         VARCHAR(20)  NOT NULL DEFAULT 'pending', -- pending | approved | rejected
  requested_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
  approved_at    TIMESTAMPTZ,
  approval_token UUID         NOT NULL DEFAULT gen_random_uuid()
);

CREATE INDEX IF NOT EXISTS idx_investor_access_email  ON investor_access (email);
CREATE INDEX IF NOT EXISTS idx_investor_access_token  ON investor_access (approval_token);

CREATE TABLE IF NOT EXISTS otp_codes (
  id          SERIAL PRIMARY KEY,
  email       VARCHAR(255) NOT NULL,
  code        CHAR(6)      NOT NULL,
  expires_at  TIMESTAMPTZ  NOT NULL,
  used        BOOLEAN      NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_otp_lookup ON otp_codes (email, used, expires_at);
