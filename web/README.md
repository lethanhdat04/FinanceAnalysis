# Giao diện web

React + Vite + TypeScript. Cần Node 20 trở lên.

```bash
# 1. Cơ sở dữ liệu và backend (từ thư mục gốc)
docker compose up -d
.venv\Scripts\python scripts\load_db.py          # lần đầu
.venv\Scripts\python -m uvicorn app.main:app --port 8000

# 2. Giao diện
cd web
npm install
npm run dev        # http://localhost:5173, /api được chuyển tới backend :8000
```
