web: playwright install chromium && uvicorn interface:app --host 0.0.0.0 --port $PORT
