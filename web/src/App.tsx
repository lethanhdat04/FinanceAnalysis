import { NavLink, Route, Routes } from 'react-router-dom'
import Assistant from './pages/Assistant'
import Calibration from './pages/Calibration'
import CaseDetail from './pages/CaseDetail'
import CaseList from './pages/CaseList'
import Reviews from './pages/Reviews'
import { USERS, useUser } from './user'

const ROLE_LABEL = { ke_toan: 'Kế toán', quan_ly: 'Quản lý' } as const

export default function App() {
  const { user, setUserId } = useUser()
  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <span>Kiểm soát chi tiêu</span>
        </div>
        <nav aria-label="Điều hướng chính">
          <NavLink to="/" end>Hồ sơ cần xem</NavLink>
          <NavLink to="/tro-ly">Trợ lý tra cứu</NavLink>
          <NavLink to="/yeu-cau-review">Yêu cầu review</NavLink>
          <NavLink to="/hieu-chinh">Hiệu chỉnh cảnh báo</NavLink>
        </nav>
        <label className="who">
          <span>Đang làm việc với vai trò</span>
          <select value={user.id} onChange={(e) => setUserId(e.target.value)}>
            {USERS.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name} ({ROLE_LABEL[u.role]})
              </option>
            ))}
          </select>
        </label>
      </aside>
      <main className="page">
        <Routes>
          <Route path="/" element={<CaseList />} />
          <Route path="/ho-so/:id" element={<CaseDetail />} />
          <Route path="/tro-ly" element={<Assistant />} />
          <Route path="/yeu-cau-review" element={<Reviews />} />
          <Route path="/hieu-chinh" element={<Calibration />} />
          <Route path="*" element={<p className="empty">Không tìm thấy trang này.</p>} />
        </Routes>
      </main>
    </div>
  )
}
