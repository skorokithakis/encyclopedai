# EncyclopedAI

An AI-powered encyclopedia that generates humorous, deliberately slightly absurd encyclopedia entries using Google's Gemini API (through the OpenAI client). The site presents itself as a legitimate encyclopedia while secretly using AI to generate content that is imperceptibly absurd while maintaining a Wikipedia-like tone.

## Features

- AI-generated encyclopedia articles with Wikipedia-like formatting
- Real-time search with intelligent matching and article suggestions
- On-demand article generation
- Markdown rendering with automatic internal linking
- Smart content previews and summaries

## Technology stack

- **Django 2.2+** - Web framework
- **Python 3.13** - Runtime
- **PostgreSQL** (production) / SQLite (development) - Database
- **Redis** - Session cache
- **Gemini API** - Content generation via the OpenAI-compatible Gemini endpoint
- **Docker** - Containerization and development environment

## Setup

### Local development with Docker

```bash
docker-compose up
```

The application will be available at http://localhost:8000.

### Local development without Docker

1. Install dependencies:
```bash
uv sync
```

2. Set environment variables:
```bash
export GEMINI_API_KEY="your-api-key"
```

3. Run migrations:
```bash
python manage.py migrate
```

4. Start the development server:
```bash
python manage.py runserver
```

### Environment variables

- `GEMINI_API_KEY` - Required. Your Gemini API key
- `GEMINI_MODEL` - Optional. Gemini model to use (default: gemini-1.5-flash)
- `GEMINI_MAX_OUTPUT_TOKENS` - Optional. Max tokens per generation (default: 8192)
- `GEMINI_API_BASE` - Optional. Override the Gemini API base URL (default points to the OpenAI-compatible endpoint)
- `DATABASE_URL` - Optional. PostgreSQL connection string for production
- `REDIS_URL` - Optional. Redis connection string for production
- `SENTRY_DSN` - Optional. Sentry error tracking

## Project structure

```
encyclopedai/
├── main/                   # Primary Django app
│   ├── models.py          # Article and User models
│   ├── views.py           # Request handlers
│   ├── services.py        # AI integration and business logic
│   └── admin.py           # Admin interface configuration
├── templates/             # HTML templates
├── static/                # CSS and static assets
├── docker-compose.yml     # Docker orchestration
└── manage.py             # Django management script
```

## How it works

1. Users search for topics through the homepage
2. Gemini acts as a reference desk, matching queries to existing articles or suggesting new ones
3. When an article doesn't exist, it's generated on-demand using Gemini
4. Articles are written in a Wikipedia-like style with deliberate subtle absurdities
5. Generated articles include internal links to other encyclopedia entries
6. All content is cached in the database for future requests

## Admin interface

The admin interface is available at `/narnia/` with full CRUD operations for articles and users.

## Testing

```bash
pytest
```

## Code quality

The project uses pre-commit hooks for code quality:

```bash
pre-commit run --all-files
```

This runs ruff (linting/formatting) and mypy (type checking).
