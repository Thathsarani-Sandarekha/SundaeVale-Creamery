# SundaeVale Finance Frontend — review milestone

Next.js 15 development interface for the fictional SundaeVale Creamery.

- Inbox / batch upload / status polling, original-PDF preview, editable extracted header/line fields, review-save with revalidation, reject, approve and approved-only CSV export.
- Approval requires a manual verification checkbox and all automated validation issues resolved.
- View audit events. The reviewer label is a fixed **unverified local demo label**, not a person who authenticated.
- All application API requests are proxied by Next.js server routes; `INVOICE_API_KEY` is never exposed as `NEXT_PUBLIC_*`.
- Setup from the repo root: start FastAPI, then `cd frontend && npm install && npm run dev`.
- `npm audit` to understand reported vulnerabilities; avoid `--force` without checking consequences.

Not production ready: no actual employee authentication, RBAC, CSRF/session security hardening, browser E2E tests, or verified OCR/Groq extraction. No real financial records should be uploaded.
