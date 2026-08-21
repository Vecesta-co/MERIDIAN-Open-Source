FROM python:3.14-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install - --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ /app/app/

# Expose the API port
EXPOSE 8000

# Default command: run the FastAPI server with uvicorn
# Can be overridden (e.g., by docker-compose to run the worker)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]