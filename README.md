# Peloton Dashboard

A local web dashboard that pulls your Peloton ride history and plots your actual power output against the instructor's cadence and resistance targets.

## What it does

- Fetches your recent cycling workouts from the Peloton API
- For structured rides (HIIT, Power Zone, Tabata), overlays the instructor's target cadence/resistance band as estimated watts
- Shows three panels:
  - **Watts over time** — actual output vs target band
  - **Band position %** — where you sat within the target (0% = floor, 100% = ceiling)
  - **Cumulative kJ** — your total energy output vs what the target band implied
- Unstructured rides (Just Ride, music/theme rides) show the output-only chart

## Requirements

- Python 3.11+
- A Peloton account
- A Bearer token from the Peloton web app (see Setup)

## Setup


### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Get your Bearer token

Peloton uses Auth0 for authentication — direct username/password API login is no longer supported. You need to grab a token from your browser session.

1. Log into [members.onepeloton.com](https://members.onepeloton.com) in Chrome or Edge
2. Open DevTools (`F12`) → **Network** tab
3. Reload the page, then click any request to `api.onepeloton.com`
4. Under **Headers**, copy the value after `Authorization: Bearer ` (it's a long string starting with `eyJ`)

Tokens expire after ~48 hours. Repeat this step when the dashboard stops working.

### 3. Create config.json

```bash
cp config.example.json config.json
```

Edit `config.json` and paste your token:

```json
{
  "active_profile": "primary",
  "profiles": {
    "primary": {
      "email": "you@example.com",
      "access_token": "<paste Bearer token here>"
    }
  }
}
```

`config.json` is gitignored and will never be committed.

### 4. Run

```bash
python -m streamlit run app.py
```

The dashboard opens automatically at `http://localhost:8501`.

## Usage

- Use the **Select a ride** dropdown to choose any of your recent cycling workouts
- Use the **Workouts to load** slider in the sidebar to fetch more history (up to 100)
- Click **Refresh workout list** to reload from the API (useful after a new ride)
- Charts are interactive — zoom, pan, and hover for exact values

## How the watt estimate works

Peloton doesn't expose instructor targets as watts — only as cadence (RPM) and resistance (%) ranges. The app converts these using a polynomial model fit from community-collected data on Peloton magnetic flywheel bikes:

```
watts ≈ -9.48 + 0.123·cadence + 0.452·resistance + 0.011·cadence·resistance + ...
```

This model won't be exact for every bike. The **Watt model accuracy** expander on each ride shows how closely it matches your actual recorded output, so you can judge how much to trust the target band position.

## Project structure

| File | Purpose |
|------|---------|
| `app.py` | Streamlit web app — UI, caching, ride selector |
| `client.py` | Peloton API client — auth, workouts, performance data, target metrics |
| `chart.py` | Plotly chart builder — target band extraction and all three subplots |
| `watt_model.py` | Cadence × resistance → watts polynomial model |
| `config.example.json` | Config template (copy to `config.json` and fill in) |

## Known limitations

- **Token expiry**: Bearer tokens last ~48 hours and must be refreshed manually from the browser
- **Watt model accuracy**: The cadence/resistance → watts conversion is an approximation; mean error is typically 10–40 W depending on your bike
- **Target data availability**: Only structured class types (HIIT, Power Zone, Tabata) have instructor target data in the API. Music/theme rides and Just Ride show output only
