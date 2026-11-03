import type { ReactNode } from 'react'
import { ApiError } from './api'

export function LevelTag({ level }: { level: string }) {
  const cls = level === 'Cảnh báo cao' ? 'tag tag-high' : level === 'Cần review' ? 'tag tag-review' : 'tag'
  return <span className={cls}>{level}</span>
}

const FINAL = new Set(['Chấp nhận', 'Đã duyệt', 'Từ chối'])
export function StatusText({ status }: { status: string }) {
  const cls = status === 'Từ chối' ? 'status status-rejected' : FINAL.has(status) ? 'status status-done' : 'status'
  return <span className={cls}>{status}</span>
}

export function RuleCodes({ codes, names }: { codes: string[]; names?: Record<string, string> }) {
  if (!codes.length) return null
  return (
    <span className="codes">
      {codes.map((c) => (
        <abbr key={c} title={names?.[c]}>
          {c}
        </abbr>
      ))}
    </span>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  const msg = error instanceof ApiError || error instanceof Error ? error.message : 'Không kết nối được tới máy chủ'
  const hint =
    error instanceof ApiError && error.status === 503
      ? 'Máy chủ đang nạp mô hình hoặc dịch vụ LLM tạm thời quá tải. Thử lại sau ít giây.'
      : error instanceof TypeError
        ? 'Kiểm tra backend đã chạy: .venv\\Scripts\\python -m uvicorn app.main:app --port 8000'
        : null
  return (
    <div className="error" role="alert">
      <strong>{msg}</strong>
      {hint && <p>{hint}</p>}
    </div>
  )
}

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      <header className="section-head">
        <h2>{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  )
}
