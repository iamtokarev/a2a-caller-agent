run:
	uv run bot.py

lint:
	uv run ruff check .

format:
	uv run ruff format .

fix:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

precommit:
	uv run pre-commit run --all-files

test:
	uv run pytest
