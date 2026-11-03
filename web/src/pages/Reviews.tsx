import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api, caseUrl, fmtDate } from '../api'
import { ErrorNote } from '../components'

const SOURCE = { tro_ly: 'Trợ lý', nguoi_dung: 'Người dùng', agent: 'Agent điều tra' } as Record<string, string>

export default function Reviews() {
  const q = useQuery({ queryKey: ['review-requests'], queryFn: api.reviewRequests })
  return (
    <>
      <header className="page-head">
        <h1>Yêu cầu review</h1>
        <p className="lede">Các yêu cầu đã được người dùng xác nhận tạo, mới nhất ở trên.</p>
      </header>
      {q.error && <ErrorNote error={q.error} />}
      {q.data && q.data.length === 0 && (
        <p className="empty">Chưa có yêu cầu nào. Hỏi trợ lý về một giao dịch và xác nhận đề xuất để tạo yêu cầu.</p>
      )}
      {q.data && q.data.length > 0 && (
        <table className="ledger">
          <thead>
            <tr>
              <th scope="col">Số</th>
              <th scope="col">Giao dịch</th>
              <th scope="col">Lý do</th>
              <th scope="col">Chứng từ cần bổ sung</th>
              <th scope="col">Gửi tới</th>
              <th scope="col">Tạo bởi</th>
              <th scope="col">Trạng thái</th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((r) => (
              <tr key={r.id}>
                <td className="num">{r.id}</td>
                <td className="nowrap">
                  {r.case_id ? <Link to={caseUrl(r.case_id)}>{r.txn_id}</Link> : r.txn_id}
                </td>
                <td>{r.reason}</td>
                <td>{r.required_documents.join(', ')}</td>
                <td>{r.route_to === 'quan_ly' ? 'Quản lý' : 'Kế toán'}</td>
                <td>
                  {r.created_by}
                  <span className="sub">
                    {SOURCE[r.source] ?? r.source}
                    {r.created_at && `, ${fmtDate(r.created_at)}`}
                  </span>
                </td>
                <td>{r.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}
