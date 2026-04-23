# Use official Playwright image (comes with Chromium pre-installed)
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

# Copy all files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port (Render uses 10000)
EXPOSE 10000

# Start app
CMD ["python", "app.py"]