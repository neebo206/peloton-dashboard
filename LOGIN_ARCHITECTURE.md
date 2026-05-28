# Peloton Dashboard — Login Architecture

## Background

Peloton migrated their authentication to **Auth0** in 2025. This closed every
previously-available programmatic login path:

| Approach | Outcome |
|---|---|
| Legacy `/auth/login` REST endpoint | 403 — "Endpoint no longer accepting requests" |
| Auth0 ROPC password grant (`/oauth/token`) | 403 — "Grant type 'password' not allowed" |
| Auth0 cross-origin auth (`/co/authenticate`) | 403 — "Cross origin login not allowed" |
| Headless Playwright (datacenter IP) | Silent block by PerimeterX bot detection |

The only path that works from a cloud host is a **headed browser** — one
with a real X11 display — because PerimeterX's fingerprinting checks pass
only when Chrome reports a non-headless rendering environment.

---

## High-Level Approach

```
User credentials → Playwright + Xvfb → Full Auth0 browser login → Capture JWT
```

The app drives a real Chrome browser through the Auth0 login UI on behalf of
the user. Playwright intercepts the network request that Chrome makes to
`api.onepeloton.com` immediately after login; the Bearer token in that
request's `Authorization` header is the Peloton API credential.

The user's password is held only in memory for the duration of the login
call. It is never stored on disk or sent anywhere except to `auth.onepeloton.com`.

---

## Login Sequence

```mermaid
sequenceDiagram
    actor User
    participant UI as Streamlit UI
    participant PW as Playwright (Python)
    participant XV as Xvfb :99
    participant CR as Chrome (headed)
    participant A0 as auth.onepeloton.com (Auth0)
    participant API as api.onepeloton.com

    User->>UI: Enter email + password, click Login
    UI->>PW: get_token_via_playwright(email, password)

    PW->>XV: Popen("Xvfb :99 -screen 0 1280x800x24")
    Note over XV: Virtual X11 display starts
    PW->>CR: Launch Chrome (headless=False, DISPLAY=:99)
    Note over CR: Chrome believes it has a real display — PerimeterX fingerprint checks pass

    CR->>A0: GET members.onepeloton.com/login
    A0-->>CR: Login page (Auth0 Universal Login)

    PW->>CR: Fill email field (press_sequentially, delay=50ms)
    PW->>CR: Fill password field (press_sequentially, delay=50ms)
    PW->>CR: Press Enter

    CR->>A0: POST credentials
    Note over A0: Auth0 OIDC authorisation flow (multiple redirect hops)

    alt Wrong credentials
        A0-->>CR: Stay on auth.onepeloton.com/login (no redirect)
        PW->>PW: Detect: URL still on /login after 20 s networkidle
        PW->>CR: browser.close()
        PW-->>UI: RuntimeError("Incorrect username or password")
    else Correct credentials
        A0-->>CR: Redirect chain → members.onepeloton.com
        CR->>API: GET api.onepeloton.com/auth/session<br/>Authorization: Bearer <JWT>
        PW->>PW: on_request() intercepts → captures JWT
        PW->>CR: browser.close()
        PW->>XV: xvfb_proc.terminate()
        PW-->>UI: Return JWT (access token)
    end

    UI->>UI: Store JWT in st.session_state.peloton_token
    UI->>UI: PelotonClient.from_token(JWT)
    UI-->>User: Dashboard renders
```

---

## Logical Architecture

```mermaid
graph TD
    subgraph User["User's Browser"]
        U[Streamlit UI]
    end

    subgraph Cloud["Streamlit Cloud Host (Linux)"]
        APP[app.py]
        CLIENT[client.py\nPelotonClient]
        SS[Session State\npeloton_token]

        subgraph Browser["Headed Browser Stack"]
            XV[Xvfb\nVirtual Display :99]
            CR[Chrome\nheadless=False]
            PW[Playwright\nsync API]
        end
    end

    subgraph Peloton["Peloton / Auth0"]
        AUTH[auth.onepeloton.com\nAuth0 OIDC]
        MEMS[members.onepeloton.com\nLogin UI]
        API[api.onepeloton.com\nREST API]
    end

    U -->|email + password| APP
    APP --> PW
    PW -->|Spawns| XV
    PW -->|Launches via DISPLAY=:99| CR
    CR -->|Login form flow| MEMS
    MEMS -->|Auth0 redirect| AUTH
    AUTH -->|Authenticated redirect| CR
    CR -->|Intercepted by on_request| PW
    PW -->|JWT Bearer token| APP
    APP --> SS
    SS --> CLIENT
    CLIENT -->|Authorization: Bearer JWT| API
    API -->|Workout + performance data| CLIENT
    CLIENT --> APP
    APP -->|Charts + metrics| U
```

---

## Token Details

The captured token is a **JSON Web Token (JWT)** — a three-part
base64url-encoded string (`header.payload.signature`).

### Relevant claims

| Claim | Value |
|---|---|
| `http://onepeloton.com/user_id` | Peloton user UUID — used to scope API calls |
| `exp` | Unix timestamp expiry (~48 h after issue) |

### Validation (`PelotonClient.token_valid`)

```python
def token_valid(token, margin_s=300):
    # Must be a 3-part JWT
    if not token or token.count(".") != 2:
        return False
    # Must not expire within the next 5 minutes
    exp = jwt_claims(token).get("exp", 0)
    return time.time() < exp - margin_s
```

If the token is near expiry when the app loads, the user is prompted to log
in again. The token itself is never persisted to disk; it lives only in
`st.session_state` for the duration of the browser session.

---

## API Usage

All Peloton API calls use a shared `requests.Session` with the token in the
`Authorization` header:

```
Authorization: Bearer <JWT>
Peloton-Platform: web
User-Agent: Mozilla/5.0 ...
```

| Endpoint | Purpose |
|---|---|
| `GET /api/user/{user_id}/workouts` | List recent cycling workouts |
| `GET /api/workout/{workout_id}/performance_graph?every_n=1` | Per-second cadence, resistance, output |
| `GET /api/ride/{ride_id}/details` | Instructor target metrics (cadence/resistance ranges per segment) |

---

## Why Xvfb Is Load-Bearing

PerimeterX (the bot-detection layer on `auth.onepeloton.com`) inspects
browser fingerprints including:

- `navigator.webdriver` flag
- Canvas and WebGL rendering signatures
- Presence of a real display in the environment

A headless Chrome process (even with anti-detection flags) is identified and
silently blocked — the login form accepts the credentials but never redirects.

Xvfb provides a real X11 framebuffer. Chrome running against it is
indistinguishable from a desktop browser from PerimeterX's perspective.
The `DISPLAY=:99` environment variable is set before Chrome launches and
cleaned up after login completes.
