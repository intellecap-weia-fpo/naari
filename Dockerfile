ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=one.settings
ENV GUNICORN_BIND=0.0.0.0:8000
ENV GUNICORN_WORKERS=3
ENV GUNICORN_TIMEOUT=120

# Set work directory
WORKDIR /app

# Install system dependencies
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
    gcc \
    pkg-config \
    libgobject-2.0-0 \
    libglib2.0-0 \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libgdk-pixbuf2.0-common \
    libpq-dev \
    curl \
 && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install Python dependencies
RUN uv sync --frozen

# Copy project
COPY . .

# Create static and media directories
RUN mkdir -p static media staticfiles

# Collect static files
RUN SECRET_KEY=build-time-only uv run python manage.py collectstatic --noinput

# Create a non-root user
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Run the application
CMD ["sh", "-c", "uv run gunicorn one.wsgi:application --bind ${GUNICORN_BIND} --workers ${GUNICORN_WORKERS} --timeout ${GUNICORN_TIMEOUT} --access-logfile - --error-logfile -"]
