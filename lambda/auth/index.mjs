/**
 * RoboDataLab investor auth Lambda
 *
 * Routes:
 *   POST /api/auth/request          — login: send OTP if approved, else status
 *   POST /api/auth/verify           — verify OTP → 30-min JWT
 *   GET  /api/auth/me               — validate JWT → { email, isAdmin }
 *   GET  /api/admin/approve/:token  — admin approves investor application
 *   GET  /api/admin/reject/:token   — admin rejects investor application
 *   POST /api/interest/register     — InvestorForm / WaitlistForm submission
 *   GET  /api/admin/users           — [admin] list investor_access rows
 *   GET  /api/admin/admins          — [admin] list admins rows
 */

import pg from 'pg'
import { SESClient, SendEmailCommand } from '@aws-sdk/client-ses'
import jwt from 'jsonwebtoken'

const { Client } = pg
const ses = new SESClient({ region: 'us-east-1' })

const ALLOWED_ORIGIN = process.env.SITE_URL ?? 'https://robodatalab.com'

const CORS = {
  'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
  'Access-Control-Allow-Headers': 'Content-Type, Authorization',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
}

// ── Schema migration ──────────────────────────────────────────────────────────

let schemaReady = false

async function ensureSchema(db) {
  if (schemaReady) return
  await db.query(`
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";

    CREATE TABLE IF NOT EXISTS investor_access (
      id             SERIAL PRIMARY KEY,
      email          VARCHAR(255) UNIQUE NOT NULL,
      name           VARCHAR(255),
      type           VARCHAR(30)  NOT NULL DEFAULT 'investor',
      status         VARCHAR(20)  NOT NULL DEFAULT 'pending',
      requested_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
      approved_at    TIMESTAMPTZ,
      approval_token UUID         NOT NULL DEFAULT gen_random_uuid()
    );

    -- Add columns that may be missing on existing tables
    ALTER TABLE investor_access ADD COLUMN IF NOT EXISTS name VARCHAR(255);
    ALTER TABLE investor_access ADD COLUMN IF NOT EXISTS type VARCHAR(30) NOT NULL DEFAULT 'investor';

    CREATE INDEX IF NOT EXISTS idx_investor_access_email ON investor_access (email);
    CREATE INDEX IF NOT EXISTS idx_investor_access_token ON investor_access (approval_token);

    CREATE TABLE IF NOT EXISTS otp_codes (
      id          SERIAL PRIMARY KEY,
      email       VARCHAR(255) NOT NULL,
      code        CHAR(6)      NOT NULL,
      expires_at  TIMESTAMPTZ  NOT NULL,
      used        BOOLEAN      NOT NULL DEFAULT FALSE,
      created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_otp_lookup ON otp_codes (email, used, expires_at);

    CREATE TABLE IF NOT EXISTS admins (
      id         SERIAL PRIMARY KEY,
      email      VARCHAR(255) UNIQUE NOT NULL,
      added_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    -- Seed initial admin
    INSERT INTO admins (email) VALUES ('ptrochim@proton.me')
    ON CONFLICT (email) DO NOTHING;
  `)
  schemaReady = true
}

// ── Entry point ───────────────────────────────────────────────────────────────

export async function handler(event) {
  const method = event.requestContext?.http?.method ?? ''
  const path   = event.rawPath ?? ''

  if (method === 'OPTIONS') {
    return { statusCode: 200, headers: CORS, body: '' }
  }

  try {
    if (method === 'POST' && path === '/api/auth/request')         return await handleRequest(event)
    if (method === 'POST' && path === '/api/auth/verify')          return await handleVerify(event)
    if (method === 'GET'  && path === '/api/auth/me')              return await handleMe(event)
    if (method === 'GET'  && path.startsWith('/api/admin/approve/')) return await handleApprove(event)
    if (method === 'GET'  && path.startsWith('/api/admin/reject/'))  return await handleReject(event)
    if (method === 'POST' && path === '/api/interest/register')    return await handleRegisterInterest(event)
    if (method === 'GET'  && path === '/api/admin/users')          return await handleAdminUsers(event)
    if (method === 'GET'  && path === '/api/admin/admins')         return await handleAdminAdmins(event)
    return json(404, { message: 'Not found' })
  } catch (err) {
    console.error('Unhandled error', err)
    return json(500, { message: 'Internal server error' })
  }
}

// ── Auth handlers ─────────────────────────────────────────────────────────────

async function handleRequest(event) {
  const body  = parseBody(event)
  const email = normalise(body?.email)
  if (!email) return json(400, { message: 'Invalid email' })

  const db = await connect()
  try {
    await ensureSchema(db)
    const { rows } = await db.query(
      'SELECT status FROM investor_access WHERE email = $1',
      [email]
    )

    if (rows.length === 0) {
      // Not in DB — tell the frontend to direct them to the sign-up form
      return json(200, { status: 'not_registered' })
    }

    const { status } = rows[0]

    if (status === 'pending')  return json(200, { status: 'pending_approval' })
    if (status === 'rejected') return json(200, { status: 'rejected' })

    if (status === 'approved') {
      const code      = randomSixDigits()
      const expiresAt = new Date(Date.now() + 10 * 60 * 1000)

      await db.query('UPDATE otp_codes SET used = TRUE WHERE email = $1 AND used = FALSE', [email])
      await db.query(
        'INSERT INTO otp_codes (email, code, expires_at) VALUES ($1, $2, $3)',
        [email, code, expiresAt]
      )
      await sendOtpEmail(email, code)
      return json(200, { status: 'otp_sent' })
    }

    return json(400, { message: 'Unknown account state' })
  } finally {
    await db.end()
  }
}

async function handleVerify(event) {
  const body  = parseBody(event)
  const email = normalise(body?.email)
  const code  = String(body?.code ?? '').trim()
  if (!email || !code) return json(400, { message: 'Missing email or code' })

  const db = await connect()
  try {
    await ensureSchema(db)
    const { rows } = await db.query(
      `SELECT id FROM otp_codes
       WHERE email = $1 AND code = $2 AND used = FALSE AND expires_at > NOW()
       ORDER BY created_at DESC LIMIT 1`,
      [email, code]
    )

    if (rows.length === 0) return json(401, { message: 'Invalid or expired code' })

    await db.query('UPDATE otp_codes SET used = TRUE WHERE id = $1', [rows[0].id])

    const isAdmin = await checkIsAdmin(db, email)
    const token   = jwt.sign({ email, isAdmin }, process.env.JWT_SECRET, { expiresIn: '30m' })
    return json(200, { token, email, isAdmin })
  } finally {
    await db.end()
  }
}

async function handleMe(event) {
  const authHeader = event.headers?.authorization ?? event.headers?.Authorization ?? ''
  if (!authHeader.startsWith('Bearer ')) return json(401, { message: 'No token' })

  try {
    const payload = jwt.verify(authHeader.slice(7), process.env.JWT_SECRET)
    // Re-check admin status live (in case it changed since token was issued)
    const db = await connect()
    try {
      await ensureSchema(db)
      const isAdmin = await checkIsAdmin(db, payload.email)
      return json(200, { email: payload.email, isAdmin })
    } finally {
      await db.end()
    }
  } catch {
    return json(401, { message: 'Token invalid or expired' })
  }
}

// ── Interest registration (InvestorForm + WaitlistForm) ───────────────────────

async function handleRegisterInterest(event) {
  const body  = parseBody(event)
  const email = normalise(body?.email)
  const name  = typeof body?.name === 'string' ? body.name.trim().slice(0, 200) : null
  const type  = body?.type === 'design_partner' ? 'design_partner' : 'investor'

  if (!email) return json(400, { message: 'Invalid email' })

  const db = await connect()
  try {
    await ensureSchema(db)
    const { rows } = await db.query(
      'SELECT status FROM investor_access WHERE email = $1',
      [email]
    )

    if (rows.length > 0) {
      // Already registered — don't overwrite, just confirm
      return json(200, { status: rows[0].status })
    }

    await db.query(
      'INSERT INTO investor_access (email, name, type) VALUES ($1, $2, $3)',
      [email, name, type]
    )
    await sendInterestNotification(email, name, type)
    return json(200, { status: 'pending_approval' })
  } finally {
    await db.end()
  }
}

// ── Admin approval / rejection ────────────────────────────────────────────────

async function handleApprove(event) {
  const token = event.rawPath.split('/').pop()
  const db    = await connect()
  try {
    await ensureSchema(db)
    const { rowCount, rows: updated } = await db.query(
      `UPDATE investor_access
         SET status = 'approved', approved_at = NOW()
       WHERE approval_token = $1 AND status = 'pending'
       RETURNING email`,
      [token]
    )

    if (rowCount === 0) return html(400, '<h1>Invalid or already-processed link.</h1>')

    await sendApprovalEmail(updated[0].email)
    return html(200, `<h1>Access approved.</h1><p>${updated[0].email} has been notified.</p>`)
  } finally {
    await db.end()
  }
}

async function handleReject(event) {
  const token = event.rawPath.split('/').pop()
  const db    = await connect()
  try {
    await ensureSchema(db)
    const { rowCount } = await db.query(
      `UPDATE investor_access SET status = 'rejected'
       WHERE approval_token = $1 AND status = 'pending'`,
      [token]
    )

    if (rowCount === 0) return html(400, '<h1>Invalid or already-processed link.</h1>')
    return html(200, '<h1>Access rejected.</h1>')
  } finally {
    await db.end()
  }
}

// ── Admin dashboard endpoints ─────────────────────────────────────────────────

async function handleAdminUsers(event) {
  const check = await requireAdmin(event)
  if (check) return check

  const db = await connect()
  try {
    await ensureSchema(db)
    const { rows } = await db.query(
      `SELECT id, email, name, type, status, requested_at, approved_at
       FROM investor_access ORDER BY requested_at DESC`
    )
    return json(200, { users: rows })
  } finally {
    await db.end()
  }
}

async function handleAdminAdmins(event) {
  const check = await requireAdmin(event)
  if (check) return check

  const db = await connect()
  try {
    await ensureSchema(db)
    const { rows } = await db.query(
      'SELECT id, email, added_at FROM admins ORDER BY added_at ASC'
    )
    return json(200, { admins: rows })
  } finally {
    await db.end()
  }
}

// ── Email helpers ─────────────────────────────────────────────────────────────

async function sendInterestNotification(email, name, type) {
  const label   = type === 'design_partner' ? 'Design Partner' : 'Investor'
  const display = name ? `${name} (${email})` : email

  await ses.send(new SendEmailCommand({
    Source:      process.env.FROM_EMAIL,
    Destination: { ToAddresses: [process.env.ADMIN_EMAIL] },
    Message: {
      Subject: { Data: `New ${label} interest: ${display}` },
      Body: {
        Html: {
          Data: `
            <p style="font-family:monospace">
              <strong>${display}</strong> has registered interest as a <strong>${label}</strong>.
            </p>
            <p style="font-family:monospace;color:#888">
              Log in to the admin console to approve or reject their access.
            </p>
          `,
        },
      },
    },
  }))
}

async function sendAdminNotification(investorEmail, approvalToken) {
  const site       = process.env.SITE_URL ?? 'https://robodatalab.com'
  const approveUrl = `${site}/api/admin/approve/${approvalToken}`
  const rejectUrl  = `${site}/api/admin/reject/${approvalToken}`

  await ses.send(new SendEmailCommand({
    Source:      process.env.FROM_EMAIL,
    Destination: { ToAddresses: [process.env.ADMIN_EMAIL] },
    Message: {
      Subject: { Data: `Investor access request: ${investorEmail}` },
      Body: {
        Html: {
          Data: `
            <p style="font-family:monospace">
              <strong>${investorEmail}</strong> has requested investor access.
            </p>
            <p>
              <a href="${approveUrl}"
                 style="display:inline-block;background:#1a1a1a;color:#fff;padding:10px 20px;
                        text-decoration:none;font-family:monospace;margin-right:12px">Approve</a>
              <a href="${rejectUrl}"
                 style="display:inline-block;background:#888;color:#fff;padding:10px 20px;
                        text-decoration:none;font-family:monospace">Reject</a>
            </p>
          `,
        },
      },
    },
  }))
}

async function sendOtpEmail(email, code) {
  await ses.send(new SendEmailCommand({
    Source:      process.env.FROM_EMAIL,
    Destination: { ToAddresses: [email] },
    Message: {
      Subject: { Data: 'Your RoboDataLab sign-in code' },
      Body: {
        Html: {
          Data: `
            <p style="font-family:monospace">Your sign-in code for RoboDataLab:</p>
            <p style="font-family:monospace;font-size:36px;font-weight:bold;
                      letter-spacing:12px;color:#1a1a1a;margin:24px 0">${code}</p>
            <p style="font-family:monospace;color:#888">
              Expires in 10&nbsp;minutes. Single use.
            </p>
          `,
        },
      },
    },
  }))
}

async function sendApprovalEmail(email) {
  const site = process.env.SITE_URL ?? 'https://robodatalab.com'
  await ses.send(new SendEmailCommand({
    Source:      process.env.FROM_EMAIL,
    Destination: { ToAddresses: [email] },
    Message: {
      Subject: { Data: 'Your RoboDataLab access has been approved' },
      Body: {
        Html: {
          Data: `
            <p style="font-family:monospace">Your access to RoboDataLab has been approved.</p>
            <p style="font-family:monospace">
              Visit <a href="${site}">${site}</a> and click <strong>Login</strong>
              in the navigation bar to sign in.
            </p>
          `,
        },
      },
    },
  }))
}

// ── Utilities ─────────────────────────────────────────────────────────────────

async function connect() {
  const client = new Client({
    host:     process.env.DB_HOST,
    database: process.env.DB_NAME,
    user:     process.env.DB_USER,
    password: process.env.DB_PASSWORD,
    port:     5432,
    ssl:      { rejectUnauthorized: false },
  })
  await client.connect()
  return client
}

async function checkIsAdmin(db, email) {
  const { rows } = await db.query(
    'SELECT 1 FROM admins WHERE email = $1',
    [email]
  )
  return rows.length > 0
}

async function requireAdmin(event) {
  const authHeader = event.headers?.authorization ?? event.headers?.Authorization ?? ''
  if (!authHeader.startsWith('Bearer ')) return json(401, { message: 'No token' })
  try {
    const payload = jwt.verify(authHeader.slice(7), process.env.JWT_SECRET)
    if (!payload.isAdmin) {
      // Re-check live in case token predates admin grant
      const db = await connect()
      try {
        const live = await checkIsAdmin(db, payload.email)
        if (!live) return json(403, { message: 'Admin access required' })
      } finally {
        await db.end()
      }
    }
    return null // OK
  } catch {
    return json(401, { message: 'Token invalid or expired' })
  }
}

function parseBody(event) {
  try { return JSON.parse(event.body ?? '{}') } catch { return {} }
}

function normalise(email) {
  if (!email || typeof email !== 'string') return null
  const t = email.trim().toLowerCase()
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(t) ? t : null
}

function randomSixDigits() {
  return String(Math.floor(100000 + Math.random() * 900000))
}

function json(status, body) {
  return {
    statusCode: status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

function html(status, content) {
  return {
    statusCode: status,
    headers: { ...CORS, 'Content-Type': 'text/html' },
    body: `<!DOCTYPE html><html><body style="font-family:sans-serif;padding:2rem;max-width:500px">${content}</body></html>`,
  }
}
