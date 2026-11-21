# Neuron

Neuron is an AI-assisted learning app. Tutor replies and duel questions are generated through a Python server that proxies requests to OpenRouter. The API key is read by the server and is never sent to the browser.

## Run locally

1. Revoke the API key that was shared in chat and create a replacement.
2. Install Python 3 if it is not already installed.
3. Double-click `start-neuron.bat`. On first run it creates `.env` from `.env.example` and opens it so you can set `OPENROUTER_API_KEY` to the replacement key. Tutor chat uses `openai/gpt-6-luna`; question generation uses `deepseek/deepseek-v4.1-flash-1017`. Keep the `:batch` suffix off these models: Batch requests are asynchronous and can take up to 24 hours, so they are not suitable for live chat or quiz questions.
4. Open `http://127.0.0.1:8000` and leave the server window open while using the app. Do not use the `file:///.../index.html` URL for AI features.

The server uses only the Python standard library. Do not publish `.env` or expose the local development server to the public internet. Ranked/fun opponents are still local simulations; real human multiplayer needs a separate authenticated matchmaking service. Account XP, rank, study time, and streak are synchronized to SQLite. Admins can set any account's level and rank from the account panel; all admin accounts are excluded server-side from both leaderboards.

## Deploy on SnapDeploy

The public source repository is [RavenGwima/neuron-ai-learning](https://github.com/RavenGwima/neuron-ai-learning). SnapDeploy builds the app from this repository with the included `Dockerfile`; GitHub is the source repository, not the web host.

1. Sign in to [SnapDeploy](https://snapdeploy.dev/register), connect GitHub, and deploy `RavenGwima/neuron-ai-learning` from the `main` branch.
2. Set `OPENROUTER_API_KEY` as a secret in SnapDeploy. Optionally set `OPENROUTER_CHAT_MODEL` and `OPENROUTER_QUESTION_MODEL`; defaults are in `.env.example`. Never commit `.env` or paste a key into the Dockerfile. Revoke any key previously shared in chat and use a replacement.
3. To bootstrap your admin account, set `NEURON_ADMIN_EMAIL` and `NEURON_ADMIN_PASSWORD` as private secrets. Use your own email and a unique password with at least 12 characters. The configured email is promoted to admin, and its password is synchronized from the secret at startup; changing the secret rotates the password. Do not share or commit either value.
4. Google sign-in is optional. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `PUBLIC_APP_URL` (the exact public HTTPS app URL). Add this exact redirect URI in Google Cloud OAuth settings: `<PUBLIC_APP_URL>/api/auth/google/callback`.
5. The app currently stores accounts, sessions, and progress in SQLite. Set `NEURON_DB_PATH` to a SQLite file on a verified persistent volume (for example, `/data/neuron.sqlite3`); otherwise replacing or waking the container may lose that data. SnapDeploy's managed PostgreSQL add-on is not supported by this SQLite-only app without a code migration.
6. Keep the free Small container. SnapDeploy supplies `HOST` and `PORT`; the container listens on the supplied port. Check `/api/health` on the generated `containers.snapdeploy.app` URL and confirm `configured` is `true`.

The app supports guest and email/password accounts; Google sign-in only appears as available when all OAuth secrets are configured. Country selection controls AI tutor and question language. Human ranked/fun matchmaking and filtering opponents by country are not implemented yet; those arena opponents are still local simulations.

The free tier provides up to 100 running hours per month across the account. A container sleeps after about 15 minutes without visits and may take about a minute to wake; Always-On hosting is paid. See [SnapDeploy pricing](https://snapdeploy.dev/pricing) and [environment variable documentation](https://snapdeploy.dev/docs/environment-variables).

In the arena, the app prepares 10 questions before the match starts. As soon as play begins, it generates the next 10 in the background; when that reserve is consumed, it keeps preparing another 10 until the match ends. The bot answers on its own timer; only the first correct answer per question gets that question's point. After 15 questions a player wins by leading by 2 points with a 2-answer correct streak. If nobody qualifies after 25 questions, the game enters Golden Round; the first correct answer wins.

LaTeX in tutor replies and AI-generated questions is rendered by MathJax from jsDelivr, so formula rendering requires an internet connection.

## Start automatically on Windows login

Double-click `setup-neuron-autostart.bat` once. It registers a per-user Task Scheduler task, starts the server hidden in the background, and starts it again on future Windows logins. It can find Python from the current user's standard Python registry installation even if `py` is not on PATH. This does not open a browser. Open `http://127.0.0.1:8000` when you want to use Neuron. To disable autostart and stop the server, run `remove-neuron-autostart.ps1` in PowerShell. The setup requires `pythonw.exe`; configure `.env` separately for AI access.
