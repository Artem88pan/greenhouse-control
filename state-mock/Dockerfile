FROM python:3.12-slim
ENV POETRY_VERSION=2.1.3
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"
WORKDIR /app
COPY pyproject.toml ./
RUN poetry config virtualenvs.create false \
 && poetry install --no-root
COPY src ./src
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
