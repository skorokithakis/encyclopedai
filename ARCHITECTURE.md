# Repository scout report

## Detected stack

- **Language:** Python 3.13+ (`pyproject.toml` → `requires-python = ">=3.13"`)
- **Framework:** Django 5.2.7 (`pyproject.toml`)
- **LLM backend:** Google Gemini, accessed via the OpenAI-compatible REST API using the `openai` Python SDK (`main/services.py`, `encyclopedai/settings.py` → `GEMINI_API_BASE`, `GEMINI_MODEL`, `GEMINI_API_KEY`)
- **Markdown rendering:** `python-markdown` with extensions `extra`, `sane_lists`, `smarty` (`main/services.py:render_article_markdown`)
- **HTML parsing:** `beautifulsoup4` — used to post-process rendered HTML for TOC extraction and heading annotation (`main/services.py:extract_toc_and_annotate_headings`)
- **Slug generation:** `shortuuid` for fallback UUIDs; custom `encyclopedai_slugify` preserving Wikipedia-style disambiguation parentheses (`main/slugs.py`)
- **Database (production):** PostgreSQL via `psycopg2-binary`; uses `ArrayField`, `GinIndex`, `TrigramSimilarity` from `django.contrib.postgres` (`main/models.py`, `main/services.py`)
- **Database (local/test):** SQLite3 (default when `IN_DOCKER` and `DATABASE_URL` are both unset)
- **Cache:** Redis via `django-redis` (production/Docker); no cache configured locally (`encyclopedai/settings.py`)
- **Static files:** WhiteNoise (`whitenoise`) for serving and compression (`encyclopedai/settings.py`, `Dockerfile`)
- **Admin search:** `djangoql` for advanced query language in the Django admin (`main/admin.py`)
- **Error tracking:** Sentry SDK with Django integration (`encyclopedai/settings.py`)
- **Math rendering:** MathJax 4 loaded from CDN (`templates/base.html`)
- **Package manager:** `uv` with `uv.lock` (`pyproject.toml`, `Dockerfile`)
- **WSGI server (production):** uWSGI, 3 workers, harakiri=120s (`misc/dokku/uwsgi.ini`)
- **Reverse proxy (dev):** Caddy (`misc/caddyfile.conf`, `docker-compose.yml`)
- **Deployment target:** Dokku (`misc/dokku/`, `.github/workflows/deploy.yml`)
- **Dev extras:** `ipython`, `bpython`, `pudb`, `django-stubs`, `werkzeug` (for `runserver_plus`)

---

## Conventions

### Formatting and linting
- **Ruff** for linting (with `--fix`) and formatting (`ruff-format`), configured in `pyproject.toml` under `[tool.ruff]`
- Ignored rules: `F403`, `E501` (no line-length limit enforced), `N802/N803/N806` (naming), `C901` (complexity), `D10x` (docstrings)
- Standard pre-commit hooks: trailing whitespace, end-of-file fixer, YAML/TOML/JSON/XML checks, merge conflict detection (`.pre-commit-config.yaml`)

### Type checking
- **Pyright** (`pyright-python` pre-commit hook, `v1.1.407`)
- `venvPath = "."`, `venv = ".venv"` (`pyproject.toml`)
- Migrations excluded from type checking (`exclude = ["**/migrations/**"]`)
- All function signatures must carry full type annotations (enforced by convention in `AGENTS.md`)
- Use built-in collection types (`list`, `dict`) not `typing` variants — though `services.py` still uses `List`, `Dict`, `Tuple` from `typing` (legacy, not yet migrated)

### Testing
- **pytest** with `pytest-django` (`pyproject.toml`)
- `DJANGO_SETTINGS_MODULE = "encyclopedai.settings"` set in `[tool.pytest.ini_options]`
- Test class names end in `Tests` (`python_classes = "*Tests"`)
- Test files: `tests.py`, `test_*.py`, `*_tests.py`
- All tests use `django.test.TestCase`; `@override_settings` used to swap out static file storage
- External dependencies (Gemini API) always mocked with `@mock.patch`
- XML test reports written to `report.xml` via `unittest-xml-reporting`
- Coverage via `pytest-cov`

### Documentation
- `AGENTS.md` at repo root: authoritative agent/developer instructions (non-negotiable narrative rules, build commands, code style)
- `README.md` at repo root (minimal)
- No separate `docs/` folder; architecture notes live in `ARCHITECTURE.md`

---

## Linting and testing commands

**Single "do everything" command (preferred):**
```bash
pre-commit run --all-files
```
(runs ruff --fix, ruff-format, pyright; defined in `.pre-commit-config.yaml`)
Run twice if the first pass auto-fixes files.

**Individual commands:**
```bash
ruff --fix .          # lint with auto-fix
ruff format .         # format
pyright               # type check
```

**Testing:**
```bash
pytest                          # all tests
pytest --cov=main               # with coverage
pytest main/tests.py::ClassName # single class
pytest -k "test_name"           # single test by name
```

---

## Project structure hotspots

```
encyclopedai/           Django project package (settings, root URLs, middleware)
├── settings.py         Central config; three DB/cache branches (local/Docker/Dokku)
├── urls.py             Root URL conf — mounts admin at /narnia/, app at /
├── middleware.py       StatsMiddleware: adds X-Page-Generation-Duration-ms header
└── context_processors.py  Injects `settings` object into every template context

main/                   The single Django app; all business logic lives here
├── models.py           Article, ArticleCreationLock, User (AbstractUser subclass)
├── services.py         All heavy logic: LLM calls, article creation, search, TOC, locking
├── views.py            Thin function-based views; delegates to services.py
├── urls.py             App-level URL patterns (namespaced as "main")
├── slugs.py            Custom slugifier preserving disambiguation parentheses
├── utils.py            User-agent whitelist for bot-blocking on fetch requests
├── admin.py            ArticleAdmin with DjangoQL search; ArticleCreationLock not registered
├── signals.py          Empty placeholder
├── tests.py            All tests in one file; ~315 lines covering views + services
├── templatetags/
│   └── markdown_filters.py  `render_markdown` template filter
└── migrations/         8 migrations; last two add outgoing_links ArrayField + data migration

templates/              Project-level templates (not app-level)
├── base.html           MathJax setup, CSS/JS includes, site name from django.contrib.sites
├── index.html          Search form + JS search flow + latest/random article lists
├── article_detail.html Article body, TOC sidebar, admin actions (staff only)
├── article_pending.html  Polling page: auto-refreshes every 2 s via setTimeout
└── fragments/
    └── spinner.html    Reusable animated spinner with rotating messages

static/
├── css/main.css        Single stylesheet
└── js/base.js          Spinner rotation helper (21 lines)

misc/
├── caddyfile.conf      Dev reverse proxy config
└── dokku/
    ├── Procfile        uWSGI launch command
    ├── uwsgi.ini       3 workers, harakiri 120 s, port 5000
    └── app.json        Dokku predeploy hook (migrate), health check, 1 web dyno

.github/workflows/
├── pre-commit.yml      Runs pre-commit --all-files on push/PR to master
└── deploy.yml          Deploys to Dokku via git push after pre-commit passes
```

---

## Database models

### `User` (`main/models.py`)
Extends `AbstractUser` with no additional fields. Registered as `AUTH_USER_MODEL`.

### `Article` (`main/models.py`)
| Field | Type | Notes |
|---|---|---|
| `id` | AutoField (int) | Primary key |
| `title` | CharField(255) | Not unique; case-insensitive lookup used in `get_or_create_article` |
| `slug` | SlugField(255) | Unique, not editable; auto-generated on first save via `encyclopedai_slugify` |
| `content` | TextField | Raw Markdown |
| `summary_snippet` | TextField | Blank allowed; LLM-generated two-sentence blurb |
| `outgoing_links` | ArrayField(CharField) | Denormalized slugs of articles this article links to; populated on every save |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

**Indexes:** Four GIN indexes — trigram on `content`, `title`, `summary_snippet` (for full-text similarity search); standard GIN on `outgoing_links` array (for reverse "what links here" lookups).

**Ordering:** `["title"]`

### `ArticleCreationLock` (`main/models.py`)
| Field | Type | Notes |
|---|---|---|
| `slug` | SlugField(255) | Unique; identifies the article being generated |
| `title` | CharField(255) | Human-readable title at lock time |
| `token` | CharField(64) | Unique; used to release only the lock you acquired |
| `expires_at` | DateTimeField | TTL = 5 minutes from creation |
| `created_at` | DateTimeField | auto_now_add |
| `updated_at` | DateTimeField | auto_now |

**Ordering:** `["slug"]`

---

## Views and URL routing

All URLs are under the `main` namespace.

| URL pattern | View | Method | Notes |
|---|---|---|---|
| `/` | `index` | GET | Search form; redirects to article on hit; shows latest (4) + random (20) articles |
| `/search/` | `search_catalogue` | GET | JSON API; calls Gemini with tool-use to return 3–6 search results |
| `/entries/from-result/` | `create_article_from_result` | POST | JSON API; creates article from search result title+snippet |
| `/entries/<path:slug>/` | `article_detail` | GET | Serves existing article or triggers generation; `<path:>` allows slashes and parentheses in slugs |
| `/entries/<path:slug>/delete/` | `article_delete` | POST | Staff only; deletes article, redirects to index |
| `/entries/<path:slug>/regenerate/` | `article_regenerate` | POST | Staff only; deletes article, redirects back to its URL to trigger regeneration |
| `/narnia/` | Django admin | — | Admin URL deliberately obscured |

**Cache-Control on article responses:** `public, max-age=86400, s-maxage=86400, stale-while-revalidate=3600` (set manually on the response object, not via Django's cache framework).

**Article pending flow:** When an article does not exist and `?fetch=1` is absent, the view returns `article_pending.html` (HTTP 202) which auto-redirects after 2 seconds with `?fetch=1` appended. On the second request the view calls `get_or_create_article` synchronously (blocking the request thread for the duration of the LLM call).

**Bot blocking:** `utils.is_whitelisted(user_agent)` checks against a hardcoded set of normalized UA patterns. Bots hitting `?fetch=1` get HTTP 403 with `article_pending.html`.

---

## Middleware

Order in `MIDDLEWARE` (`encyclopedai/settings.py`):

1. `SecurityMiddleware` (Django)
2. **`StatsMiddleware`** (`encyclopedai/middleware.py`) — records `request.start_time`; adds `X-Page-Generation-Duration-ms` response header
3. `WhiteNoiseMiddleware` — static file serving
4. `SessionMiddleware`
5. `CommonMiddleware`
6. `CurrentSiteMiddleware` — populates `request.site` (used in templates for site name)
7. `AuthenticationMiddleware`
8. `MessageMiddleware`
9. `XFrameOptionsMiddleware`

No CSRF middleware is present (intentionally removed; CSRF tokens are passed manually via `get_token()` in the index view for the JS search form).

---

## Caching configuration

**Local (no env vars):** No cache configured; Django uses in-memory/dummy cache by default.

**Docker (`IN_DOCKER=1`):**
- Backend: `django_redis.cache.RedisCache`
- Location: `redis://redis/1`
- Session engine: `cached_db` (DB-backed with cache layer)
- Session cookie age: 1 year

**Dokku (`DATABASE_URL` set):**
- Backend: `django_redis.cache.RedisCache`
- Location: `REDIS_URL` env var
- Session engine: `cache` (cache-only, no DB fallback)
- Session cookie age: 1 year; `SESSION_COOKIE_SECURE = True`

Django's cache framework is **not** used for view-level caching. HTTP `Cache-Control` headers are set directly on responses in `views.py` for CDN/browser caching of article pages.

---

## Services and background tasks

**There are no background tasks, Celery workers, cron jobs, or async task queues.** All LLM calls happen synchronously within the request/response cycle, blocking the worker thread for the duration of the Gemini API call (up to the uWSGI harakiri limit of 120 seconds).

### `main/services.py` — key functions

| Function | Purpose |
|---|---|
| `get_or_create_article(topic, summary_hint, slug_hint, user)` | Main entry point: looks up existing article by title/slug, enforces daily limit, acquires lock, calls Gemini, saves article |
| `generate_article_content(topic, summary_hint, link_briefings)` | Two-shot Gemini call: first generates draft, then adds internal cross-reference links |
| `generate_article_summary(title, article_body)` | Single Gemini call to produce a ≤320-char summary snippet |
| `generate_search_results(query)` | Gemini tool-use call returning 3–6 search result objects; pre-seeds with trigram-matched existing articles |
| `enforce_daily_article_limit(user)` | Counts today's articles; raises `DailyArticleLimitExceeded` for anonymous users at/above `DAILY_ARTICLE_LIMIT` (default 500) |
| `_acquire_article_creation_lock(slug, title)` | `select_for_update()` within `transaction.atomic()`; raises `ArticleCreationInProgress` if a live lock exists |
| `_release_article_creation_lock(slug, token)` | Deletes the lock row matching slug+token |
| `_collect_incoming_link_briefings(target_slug)` | Queries `outgoing_links__contains=[slug]` (GIN index) to find articles linking here; extracts context snippets for the LLM |
| `extract_toc_and_annotate_headings(markdown_content)` | Renders Markdown → HTML, adds `id` attributes to H2–H6, returns annotated HTML + TOC list |
| `render_article_markdown(markdown_text)` | Converts Markdown to safe HTML; disables raw HTML blocks/inline HTML |
| `cleanup_article_body(text)` | Strips leading heading + normalizes domain-qualified `/entries/` links to site-relative |
| `humanize_slug(slug)` | Converts slug back to title-cased display string |

### Locking mechanism
`ArticleCreationLock` rows act as a distributed mutex (TTL 5 minutes). Expired locks are cleaned up at the start of each `_acquire_article_creation_lock` call. The lock is always released in a `finally` block. This prevents duplicate article generation when multiple requests race for the same slug.

### LLM integration
- Uses the OpenAI Python SDK pointed at Gemini's OpenAI-compatible endpoint
- Model, max tokens, and API base URL are all configurable via environment variables
- Two-pass generation: content draft → link enrichment pass
- Tool-use (function calling) is used for structured search result output
- All Gemini calls are wrapped in broad `except Exception` with `logger.exception` + re-raise as `RuntimeError` (the only place broad exception catching is used, explicitly for defensive logging at the API boundary)

---

## Do and don't patterns

### Do

- **Thin views, fat services:** Views call `services.py` functions and handle HTTP concerns (redirects, status codes, template rendering). All business logic, LLM calls, and DB orchestration live in `services.py`. (`main/views.py`, `main/services.py`)

- **Specific exception handling:** Custom domain exceptions (`ArticleCreationInProgress`, `DailyArticleLimitExceeded`) are raised and caught by name. Django's `ImproperlyConfigured`, `ValueError`, `RuntimeError` are caught specifically at view boundaries. (`main/views.py:57–193`, `main/services.py:50–69`)

- **Module-level logger:** `logger = logging.getLogger(__name__)` at the top of `services.py`. (`main/services.py:38`)

- **`select_for_update()` + `transaction.atomic()` for locking:** Used in `_acquire_article_creation_lock` to prevent race conditions. (`main/services.py:82–101`)

- **`save(update_fields=[...])` for partial updates:** Used throughout `services.py` and `views.py` to avoid full-row writes. (`main/services.py:94`, `main/views.py:144`)

- **Denormalization for query performance:** `outgoing_links` ArrayField is populated on every `Article.save()` and indexed with GIN to enable fast reverse link lookups without full-text scanning. (`main/models.py:39–43`, `main/services.py:312`)

- **Custom slugifier preserving parentheses:** `encyclopedai_slugify` in `main/slugs.py` handles Wikipedia-style disambiguation like `mercury-(planet)` correctly, where Django's built-in `slugify` would strip the parentheses.

- **Environment-driven configuration:** Three distinct DB/cache configurations selected by env vars (`IN_DOCKER`, `DATABASE_URL`). No hardcoded production secrets. (`encyclopedai/settings.py`)

- **`mark_safe` only at the rendering boundary:** HTML is only marked safe after going through the Markdown renderer or BeautifulSoup, never on raw user input. (`main/services.py:893`, `main/services.py:907`, `main/services.py:945`)

### Don't

- **No broad exception swallowing:** The only `except Exception` blocks are at the Gemini API call boundary, and they always re-raise as `RuntimeError` after logging. Everywhere else, specific exception types are caught. (`main/services.py:421–423`, `main/services.py:448–450`)

- **No Celery / async tasks:** All work is synchronous and in-process. There is no task queue, no `@shared_task`, no `async def` views.

- **No Django cache framework for views:** View caching is done via HTTP headers (`Cache-Control`), not `@cache_page` or `cache.set()`. The Redis cache is used only for sessions.

- **No `nullable` string fields:** String fields use `blank=True, default=""` rather than `null=True`. (`main/models.py:36`, `main/models.py:43`)

- **No imports inside functions** (except in `content_preview` property and `strip_leading_heading` where `re` is imported locally — these are minor inconsistencies with the stated convention).

- **No CSRF middleware:** Removed from the middleware stack; CSRF tokens are injected manually via `get_token()` for the JS search form. This is intentional but means CSRF protection must be managed carefully for any new POST endpoints.

---

## Open questions

- **`ArticleCreationLock` not in admin:** The model exists and is used, but is not registered in `main/admin.py`. This may be intentional (operational concern) or an oversight.
- **`main/signals.py` is empty:** The file exists as a placeholder. No signals are currently used.
- **`main/management/commands/` is empty** (only `__pycache__`): No custom management commands exist despite the directory being present.
- **No `ArticleCreationLock` admin registration:** If a lock gets stuck (e.g., due to a crash before `finally`), there is no admin UI to clear it — only direct DB access or waiting for the 5-minute TTL.
- **Synchronous LLM calls in request thread:** Under load, a single uWSGI worker (of 3) can be blocked for up to 120 seconds waiting for Gemini. This limits concurrency significantly. Whether this is acceptable depends on expected traffic.
