# Deployment

How to run the dashboard somewhere a recruiter can click. This doc covers the public hosted demo (Fly.io); for local-only or self-host scenarios, see [docs/usage.md](usage.md).

## Contents

- [Deployment posture](#deployment-posture)
- [Fly.io — the recommended path](#flyio--the-recommended-path)
  - [One-time setup](#one-time-setup)
  - [Deploy + redeploy](#deploy--redeploy)
  - [Custom domain](#custom-domain)
  - [Cost expectations](#cost-expectations)
- [Why not Streamlit Cloud](#why-not-streamlit-cloud)
- [Other Docker-friendly platforms](#other-docker-friendly-platforms)

---

## Deployment posture

The shipped `Dockerfile` deploys to any container platform — Fly.io, Render, Railway, Hugging Face Spaces, your own VPS. A few decisions are baked in:

- **XSRF protection is on by default.** Earlier revisions disabled it with a "safe behind Docker network" comment, which was honest in a `docker compose up on a laptop` world but wrong as soon as the image lands on a public URL. Re-enabled at the image level so any deployment inherits the safe default.
- **CORS is disabled.** Streamlit's CORS toggle is for cross-origin Streamlit-to-Streamlit traffic; doesn't apply to a single-app deploy.
- **The container binds to `0.0.0.0:8501`.** The platform layer (Fly/Render/etc.) maps that to public 80/443.
- **Healthcheck uses Streamlit's `/_stcore/health` endpoint.** The Dockerfile has its own `HEALTHCHECK` for local `docker run`; the platform-side healthcheck (defined in `fly.toml` for Fly) is what actually gates rolling deploys.
- **State is ephemeral.** The image doesn't mount a volume; each restart starts fresh. This is fine for a portfolio demo where audit-trail persistence is in-trace, not on-disk-between-runs. For a multi-tenant production deploy you'd add a Fly volume or equivalent.

## Fly.io — the recommended path

Fly is the right pick for a portfolio because:

- **No cold starts** when `min_machines_running = 1` is set.
- **Cheap** — a 512MB shared-CPU VM runs ~$3.89/mo, well within the $5/mo Hobby plan credit.
- **Docker-native** — uses our existing `Dockerfile`, no per-platform build config needed.
- **Custom domain support** — point `demo.yourdomain.com` at the app for a clean portfolio link.
- **CLI + GitHub Action** for redeploys; no web-UI babysitting.

### One-time setup

```bash
# 1. Install flyctl (macOS)
brew install flyctl

# 2. Sign up / log in
fly auth signup    # or: fly auth login

# 3. From the repo root, create the app using our pre-baked fly.toml
#    --copy-config keeps OUR fly.toml (don't let `fly launch` overwrite it).
#    --no-deploy lets us set secrets before the first build.
fly launch --copy-config --no-deploy
# Pick a unique app name (e.g., autonomy-engine-demo). Edit `app = "..."` in
# fly.toml to match if `fly launch` didn't already update it.

# 4. Set the API key as a Fly secret. Never commit this to the repo.
fly secrets set ANTHROPIC_API_KEY="sk-ant-..."
# Optional, only if you also want OpenAI fallback:
fly secrets set OPENAI_API_KEY="sk-..."

# 5. First deploy
fly deploy
```

After ~3–5 minutes (Docker layer build + image push + machine start), Fly prints the URL. Default form: `https://<app-name>.fly.dev`.

### Deploy + redeploy

After the initial setup, every code change deploys with:

```bash
fly deploy
```

To wire up GitHub Actions for auto-deploy on push to `main`, see [Fly.io's continuous-deployment docs](https://fly.io/docs/app-guides/continuous-deployment-with-github-actions/) — TL;DR: add a `FLY_API_TOKEN` repo secret and a workflow file that calls `flyctl deploy --remote-only`.

### Custom domain

Once the app is running:

```bash
fly certs create demo.yourdomain.com
# Follow the DNS instructions Fly prints (one A record + one AAAA record).
```

Cert auto-provisions via Let's Encrypt in 1–2 minutes after DNS propagates.

### Cost expectations

Per [Fly's pricing page](https://fly.io/docs/about/pricing/), a `shared-cpu-1x` VM with 512MB RAM running 24/7 is ~$3.89/mo. The Hobby plan includes a $5/mo usage credit, so a single always-on demo VM is **effectively free**.

If you also wire up the Docker sandbox (`sandbox.backend: docker` in `config.yml`) you'd need a beefier plan and/or a separate sandbox host — but for the portfolio demo, `sandbox.backend: local` is the right default and the demo Dockerfile uses that.

## Why not Streamlit Cloud

Streamlit Cloud is the obvious-looking choice but has two problems for a portfolio:

1. **Cold starts.** Free-tier apps sleep after ~7 days of inactivity. First visitor after that waits ~30 seconds on a splash screen. Recruiter UX is bad.
2. **Build-system auto-detection.** Streamlit Cloud picks Poetry when it sees `pyproject.toml`; our project uses setuptools with a flat-package layout (`engine/`, `tasks/`, `graph/`, `dashboard/`, `intake/`), and Poetry's `Installing the current project` step fails to find a directory matching the package name. The fix in commit history ([2e938ea](https://github.com/Kaden-G/autonomy_engine/commit/2e938ea)) renamed `requirements.lock` → `requirements.txt` so Streamlit Cloud uses pip instead, but you still get cold starts.

The paid tier ($20/mo) keeps apps warm. At that price, Fly.io is the better deal.

## Other Docker-friendly platforms

The same image deploys cleanly to:

- **Render** — Starter Web Service ($7/mo), no cold starts, smoothest web-UI workflow. Good if you'd rather not learn `flyctl`.
- **Railway** — Hobby plan (~$5/mo), similar to Render.
- **Hugging Face Spaces** — free, but cold starts; same UX problem as Streamlit Cloud.
- **Self-hosted VPS** — Hetzner ($5/mo), DigitalOcean ($6/mo). More control, more babysitting.

For each, the only platform-specific work is the equivalent of `fly.toml` (`render.yaml`, `railway.json`, etc.) and where you set the `ANTHROPIC_API_KEY` secret.
