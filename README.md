# Neuron

Neuron is an AI-assisted learning app. Tutor replies and duel questions are generated through a Python server that proxies requests to OpenRouter. The API key is read by the server and is never sent to the browser.

## Run locally

1. Revoke the API key that was shared in chat and create a replacement.
2. Install Python 3 if it is not already installed.
3. Double-click `start-neuron.bat`. On first run it creates `.env` from `.env.example` and opens it so you can set `OPENROUTER_API_KEY` to the replacement key. Tutor chat uses `openai/gpt-6-luna`; question generation uses `deepseek/deepseek-v4.1-flash-1017`. Keep the `:batch` suffix off these models: Batch requests are asynchronous and can take up to 24 hours, so they are not suitable for live chat or quiz questions.
4. Open `http://127.0.0.1:8000` and leave the server window open while using the app. Do not use the `file:///.../index.html` URL for AI features.

The server uses only the Python standard library. Do not publish `.env` or expose the local development server to the public internet. The current ranked/fun opponents and leaderboards remain local simulations; real multiplayer needs a separate authenticated matchmaking service.

## Deploy publicly

The public source repository is [RavenGwima/neuron-ai-learning](https://github.com/RavenGwima/neuron-ai-learning). GitHub Pages cannot run the Python API, so deploy the complete app (frontend and API) as a Render web service using the included `render.yaml`:

1. Open [Deploy to Render](https://render.com/deploy?repo=https://github.com/RavenGwima/neuron-ai-learning) and connect your GitHub account.
2. Set `OPENROUTER_API_KEY` as a secret when prompted. Never commit `.env` or paste the key into `render.yaml`.
3. Open the public `onrender.com` URL after Render finishes deploying. The free instance may sleep when idle and take a little time to wake up.

The deployed service reads Render's `PORT` and `RENDER_EXTERNAL_URL` environment variables. Rotate any API key previously shared in chat before adding the replacement as a Render secret.

In the arena, the next AI question is requested while the current question is open and appears as soon as the answer resolves. After 15 questions a player wins by leading by 2 points with a 2-answer correct streak. If nobody qualifies after 25 questions, the game enters Golden Round; the first correct answer wins.

LaTeX in tutor replies and AI-generated questions is rendered by MathJax from jsDelivr, so formula rendering requires an internet connection.

## Start automatically on Windows login

Double-click `setup-neuron-autostart.bat` once. It registers a per-user Task Scheduler task, starts the server hidden in the background, and starts it again on future Windows logins. It can find Python from the current user's standard Python registry installation even if `py` is not on PATH. This does not open a browser. Open `http://127.0.0.1:8000` when you want to use Neuron. To disable autostart and stop the server, run `remove-neuron-autostart.ps1` in PowerShell. The setup requires `pythonw.exe`; configure `.env` separately for AI access.
