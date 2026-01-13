# EncyclopedAI Agent Instructions

This is a tongue-in-cheek encyclopedia. There must be no references to generating these
articles, the site must pretend to be a legitimate site, even sometimes going slightly
over the top.

## Build and Test Commands

### Dependencies
```bash
uv sync
```

### Database
```bash
python manage.py migrate
python manage.py makemigrations
```

### Testing
```bash
# Run all tests
pytest

# Run single test
pytest main/tests.py::IndexViewTests::test_index_redirects_to_existing_article
pytest -k "test_index_redirects_to_existing_article"

# Run tests in a class
pytest main/tests.py::IndexViewTests

# Run with coverage
pytest --cov=main
```

### Code Quality (run in order, repeat if errors)
```bash
pre-commit run --all-files
```

This runs:
- ruff --fix (linter with auto-fix)
- ruff format (formatter)
- pyright (type checker)

Or run individually:
```bash
ruff --fix .
ruff format .
pyright
```

## Code Style Guidelines

### Formatting and Imports
- 4 spaces, max 90 characters per line, LF line endings, trim trailing whitespace
- Import order: standard library, third-party, local (blank lines between groups)
- Type hints required for all functions and class methods
- Use `from typing import List, Dict, Optional` explicitly

### Naming Conventions
- Classes: PascalCase (ArticleCreationLock, DailyArticleLimitExceeded)
- Functions/methods: snake_case (get_or_create_article, extract_outgoing_links)
- Constants: UPPER_CASE at module level (ARTICLE_CREATION_LOCK_TTL)
- Private functions: leading underscore (_acquire_article_creation_lock)
- Model fields: snake_case matching Django conventions

### Django Patterns
- Models: define Meta with ordering and indexes, include __str__, get_absolute_url()
- Save method: override for custom logic (slug generation, denormalization)
- Update fields: use save(update_fields=["field1", "field2"]) for partial updates
- Queries: use select_for_update() within transaction.atomic() for locking
- Views: function-based with type hints on parameters, use Django shortcuts
- Admin: register all model fields for completeness
- Transactions: use @transaction.atomic or with transaction.atomic()
- Error handling: catch ImproperlyConfigured, ValueError, RuntimeError appropriately

### Business Logic
- Keep views thin, move logic to services.py module
- Create custom exceptions for domain errors (ArticleCreationInProgress)
- Use module-level logger: `logger = logging.getLogger(__name__)`
- Define patterns as module-level constants with leading underscore for internal

### Testing
- Use Django's TestCase with @override_settings decorator for storage
- Mock external dependencies: `@mock.patch("path.to.function")`
- Test class names end with Tests (IndexViewTests, ArticleSlugTests)
- Test methods start with test_ and use descriptive names
- Use self.client for HTTP requests, self.assertRedirects/assertContains for assertions

### Error Handling
- Don't use forgiving try/catch blocks - let exceptions propagate
- Catch specific exceptions, not broad Exception
- Raise custom exceptions for business logic failures
- Use Django's ImproperlyConfigured for missing settings

### Database
- Avoid N+1 queries - use select_related/prefetch_related
- Use index-only queries where possible
- Denormalize when performance is critical (outgoing_links field)
- Use GIN indexes for array/text search fields in PostgreSQL

### Important Notes
- NEVER run `manage.py runserver` for testing
- ALWAYS run pre-commit hooks after changes
- Model fields must be exposed in admin
- Use gettext for translatable strings: `from django.utils.translation import gettext as _`
- NEVER read or display the .envrc file - it contains sensitive API keys
