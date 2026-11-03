import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, caseUrl, fmtDate, fmtMonth, money } from '../api'
import { ErrorNote, LevelTag, RuleCodes, StatusText } from '../components'

const PAGE_SIZE = 25
const FILTERS = ['level', 'status', 'kind', 'month', 'rule', 'q'] as const

export default function CaseList() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const page = Number(params.get('page') ?? 1)
  const filters = Object.fromEntries(FILTERS.map((k) => [k, params.get(k) ?? '']).filter(([, v]) => v))

  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const stats = useQuery({ queryKey: ['stats', filters.month], queryFn: () => api.stats(filters.month) })
  const list = useQuery({
    queryKey: ['cases', filters, page],
    queryFn: () => api.cases({ ...filters, page: String(page), size: String(PAGE_SIZE) }),
    placeholderData: keepPreviousData,
  })

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    next.delete('page')
    setParams(next)
  }
  const setPage = (p: number) => {
    const next = new URLSearchParams(params)
    next.set('page', String(p))
    setParams(next)
  }
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE_SIZE)) : 1

  return (
    <>
      <header className="page-head">
        <h1>Hồ sơ cần xem</h1>
        <p className="lede">
          Giao dịch bị quy tắc hoặc mô hình bất thường đưa vào diện kiểm tra. Mỗi cụm mua dồn trong ngày của một người chi là
          một hồ sơ.
        </p>
      </header>

      {stats.data && (
        <dl className="tally">
          <div>
            <dt>Cảnh báo cao</dt>
            <dd className="num high">{stats.data.by_level['Cảnh báo cao'].toLocaleString('vi-VN')}</dd>
          </div>
          <div>
            <dt>Cần review</dt>
            <dd className="num review">{stats.data.by_level['Cần review'].toLocaleString('vi-VN')}</dd>
          </div>
          {stats.data.by_level['Bình thường'] > 0 && (
            <div>
              <dt>Đã hạ mức do hiệu chỉnh</dt>
              <dd className="num muted">{stats.data.by_level['Bình thường'].toLocaleString('vi-VN')}</dd>
            </div>
          )}
          <div>
            <dt>Chưa xử lý</dt>
            <dd className="num">{stats.data.by_status['Chờ review'].toLocaleString('vi-VN')}</dd>
          </div>
          <div>
            <dt>Chờ quản lý phê duyệt</dt>
            <dd className="num">{stats.data.by_status['Chờ phê duyệt'].toLocaleString('vi-VN')}</dd>
          </div>
          <div>
            <dt>Trên tổng số giao dịch</dt>
            <dd className="num muted">{stats.data.transactions.toLocaleString('vi-VN')}</dd>
          </div>
        </dl>
      )}

      <form className="filters" onSubmit={(e) => e.preventDefault()} aria-label="Lọc hồ sơ">
        <label>
          Tìm
          <input
            type="search"
            placeholder="Cửa hàng, người chi, đơn vị, mã giao dịch"
            defaultValue={filters.q ?? ''}
            onKeyDown={(e) => e.key === 'Enter' && setFilter('q', e.currentTarget.value.trim())}
            onBlur={(e) => e.currentTarget.value.trim() !== (filters.q ?? '') && setFilter('q', e.currentTarget.value.trim())}
          />
        </label>
        <label>
          Mức rủi ro
          <select value={filters.level ?? ''} onChange={(e) => setFilter('level', e.target.value)}>
            <option value="">Tất cả</option>
            <option>Cảnh báo cao</option>
            <option>Cần review</option>
            <option value="Bình thường">Đã hạ mức (hiệu chỉnh)</option>
          </select>
        </label>
        <label>
          Trạng thái
          <select value={filters.status ?? ''} onChange={(e) => setFilter('status', e.target.value)}>
            <option value="">Tất cả</option>
            {meta.data?.statuses.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <label>
          Loại hồ sơ
          <select value={filters.kind ?? ''} onChange={(e) => setFilter('kind', e.target.value)}>
            <option value="">Tất cả</option>
            <option value="giao_dich">Một giao dịch</option>
            <option value="cum_mua_don">Cụm mua dồn</option>
          </select>
        </label>
        <label>
          Tháng
          <select value={filters.month ?? ''} onChange={(e) => setFilter('month', e.target.value)}>
            <option value="">Tất cả</option>
            {meta.data?.months.map((m) => (
              <option key={m} value={m}>
                {fmtMonth(m)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Quy tắc
          <select value={filters.rule ?? ''} onChange={(e) => setFilter('rule', e.target.value)}>
            <option value="">Tất cả</option>
            {meta.data &&
              Object.entries(meta.data.rules).map(([k, v]) => (
                <option key={k} value={k}>
                  {k} {v}
                </option>
              ))}
          </select>
        </label>
      </form>

      {list.error && <ErrorNote error={list.error} />}

      {list.data && (
        <>
          <p className="count" aria-live="polite">
            {list.data.total.toLocaleString('vi-VN')} hồ sơ
            {list.isFetching && <span className="muted"> đang tải</span>}
          </p>
          {list.data.items.length === 0 ? (
            <p className="empty">Không có hồ sơ nào khớp bộ lọc. Bỏ bớt điều kiện lọc để xem thêm.</p>
          ) : (
            <table className="ledger">
              <thead>
                <tr>
                  <th scope="col">Mức</th>
                  <th scope="col">Hồ sơ</th>
                  <th scope="col">Ngày</th>
                  <th scope="col" className="r">
                    Số tiền
                  </th>
                  <th scope="col">Quy tắc</th>
                  <th scope="col">Mô hình</th>
                  <th scope="col">Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((c) => (
                  <tr
                    key={c.id}
                    className={c.level === 'Cảnh báo cao' ? 'row-high' : c.level === 'Cần review' ? 'row-review' : 'row-lowered'}
                    onClick={() => navigate(caseUrl(c.id))}
                  >
                    <td>
                      <LevelTag level={c.level} />
                    </td>
                    <td className="case-cell">
                      <Link to={caseUrl(c.id)} onClick={(e) => e.stopPropagation()}>
                        {c.title.split(' · ')[0]}
                      </Link>
                      {c.title.includes(' · ') && <span className="item">{c.title.split(' · ').slice(1).join(' ')}</span>}
                      <span className="sub">
                        {c.cardholder_id.split('|').slice(1).join(' ')} – {c.agency}
                      </span>
                    </td>
                    <td className="nowrap">{fmtDate(c.tx_date)}</td>
                    <td className="r num">
                      {money(c.total_amount)}
                      {c.n_txn > 1 && <span className="sub">{c.n_txn} giao dịch</span>}
                    </td>
                    <td>
                      <RuleCodes codes={c.rules} names={meta.data?.rules} />
                    </td>
                    <td>{c.has_anomaly ? (c.kind === 'cum_mua_don' ? 'Mua dồn' : 'Bất thường') : ''}</td>
                    <td>
                      <StatusText status={c.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <nav className="pager" aria-label="Phân trang">
            <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Trang trước
            </button>
            <span>
              Trang {page} / {pages.toLocaleString('vi-VN')}
            </span>
            <button type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>
              Trang sau
            </button>
          </nav>
        </>
      )}
    </>
  )
}
