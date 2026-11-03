import { createContext, useContext, useState, type ReactNode } from 'react'
import type { Role } from './api'

// Đồ án chưa có đăng nhập: chọn người dùng mẫu để thử các vai trò.
export const USERS: { id: string; name: string; role: Role }[] = [
  { id: 'ke_toan_1', name: 'Nguyễn Thu Hà', role: 'ke_toan' },
  { id: 'ke_toan_2', name: 'Phạm Đức Long', role: 'ke_toan' },
  { id: 'quan_ly_1', name: 'Trần Minh Quân', role: 'quan_ly' },
]

type Ctx = { user: (typeof USERS)[number]; setUserId: (id: string) => void }
const UserContext = createContext<Ctx | null>(null)

function initialId() {
  try {
    return localStorage.getItem('userId') ?? USERS[0].id
  } catch {
    return USERS[0].id
  }
}

export function UserProvider({ children }: { children: ReactNode }) {
  const [id, setId] = useState(initialId)
  const user = USERS.find((u) => u.id === id) ?? USERS[0]
  const setUserId = (next: string) => {
    setId(next)
    try {
      localStorage.setItem('userId', next)
    } catch {
      /* bỏ qua nếu trình duyệt chặn lưu trữ */
    }
  }
  return <UserContext.Provider value={{ user, setUserId }}>{children}</UserContext.Provider>
}

export function useUser() {
  const ctx = useContext(UserContext)
  if (!ctx) throw new Error('useUser phải nằm trong UserProvider')
  return ctx
}
