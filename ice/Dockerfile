# Dockerfile — ICE API image (spec Part 4 §19.1).
# Serves: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2

FROM python:3.12-slim

WORKDIR /app

# GL/GLX + glib shared libraries, needed by the optional docling parsing stack.
# NOTE: spec §19.1 names libgl1-mesa-glx; that package was removed in Debian 12
# (bookworm — the base of python:3.12-slim). libgl1 + libglx-mesa0 are the
# bookworm successors providing the same libGL/libGLX runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglx-mesa0 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first so this layer is cached across source edits.
# compat-mode editable install: emits a .pth pointing at /app instead of the
# strict PEP 660 finder (which would snapshot the package list — still empty at
# this layer — and fail to resolve packages copied in the NEXT layer).
COPY pyproject.toml poetry.lock* ./
RUN pip install --no-cache-dir -e . --config-settings editable_mode=compat

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]