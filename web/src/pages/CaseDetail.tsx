import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, fmtDate, money, type CaseDetail as Detail, type Explanation, type Meta, type RuleHit } from '../api'
import { ErrorNote, LevelTag, RuleCodes, Section, StatusText } from '../components'
import { useUser } from '../user'
import AgentPanel from './AgentPanel'

const SEVERITY_RANK = { Cao: 0, 'Trung bình': 1, Thấp: 2 } as const
const SOURCE_LABEL = { rule: 'Quy tắc', mo_hinh: 'Mô hình', phan_loai: 'Phân loại', quy_che: 'Quy chế' } as const

export default function CaseDetail() {
  const id = decodeURIComponent(useParams().id ?? '')
  const { user } = useUser()
  const meta = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const detail = useQuery({ queryKey: ['case', id, user.role], queryFn: () => api.caseDetail(id, user.role) })

  if (detail.error) return <ErrorNote error={detail.error} />
  if (!detail.data || !meta.data) return <p className="muted">Đang tải hồ sơ…</p>
  const d = detail.data
  const c = d.case

  return (
    <>
      <Link to="/" className="back">
        Quay lại danh sách
      </Link>
      <header className="case-head">
        <div className="case-title">
          <LevelTag level={c.level} />
          <h1>{c.title.split(' · ')[0]}</h1>
          {c.title.includes(' · ') && <p className="item-desc">{c.title.split(' · ').slice(1).join(' ')}</p>}
        </div>
        <dl className="facts">
          <div>
            <dt>Người chi</dt>
            <dd>{c.cardholder_id.split('|').slice(1).join(' ')}</dd>
          </div>
          <div>
            <dt>Đơn vị</dt>
            <dd>{c.agency}</dd>
          </div>
          <div>
            <dt>Ngày giao dịch</dt>
            <dd>{fmtDate(c.tx_date)}</dd>
          </div>
          <div>
            <dt>{c.n_txn > 1 ? `Tổng ${c.n_txn} giao dịch` : 'Số tiền'}</dt>
            <dd className="num">{money(c.total_amount)}</dd>
          </div>
          <div>
            <dt>Trạng thái</dt>
            <dd>
              <StatusText status={c.status} />
              {c.assignee && <span className="muted"> ({c.assignee})</span>}
            </dd>
          </div>
        </dl>
      </header>

      <div className="case-grid">
        <div className="case-main">
          <Findings d={d} meta={meta.data} />
          <AiMemo d={d} />
          <AgentPanel d={d} meta={meta.data} />
          <Transactions d={d} meta={meta.data} />
        </div>
        <aside className="case-side">
          <ActionPanel d={d} meta={meta.data} />
          <History d={d} meta={meta.data} />
        </aside>
      </div>
    </>
  )
}

// ---------------------------------------------------------------- hệ thống phát hiện gì
function Findings({ d, meta }: { d: Detail; meta: Meta }) {
  // Gộp theo (quy tắc, mức độ): một dòng đại diện + số giao dịch cùng loại, tránh lặp lại trong cụm mua dồn
  const groups = new Map<string, { hit: RuleHit & { txn_id: string }; n: number }>()
  for (const t of d.transactions) {
    for (const h of t.rule_hits) {
      const key = `${h.rule_id}|${h.severity}`
      const g = groups.get(key)
      if (g) g.n += 1
      else groups.set(key, { hit: { ...h, txn_id: t.txn_id }, n: 1 })
    }
  }
  const shown = [...groups.values()].sort(
    (a, b) => SEVERITY_RANK[a.hit.severity] - SEVERITY_RANK[b.hit.severity] || a.hit.rule_id.localeCompare(b.hit.rule_id),
  )
  const a = d.analysis
  return (
    <Section title="Hệ thống phát hiện gì">
      <p className="hint">Các kết luận dưới đây do quy tắc và mô hình tính ra, không phải do AI tạo văn bản.</p>
      {shown.length > 0 && (
        <ul className="findings">
          {shown.map(({ hit: h, n }) => (
            <li key={`${h.rule_id}|${h.severity}`} className={`sev sev-${SEVERITY_RANK[h.severity]}`}>
              <span className="finding-code">
                {h.rule_id} {meta.rules[h.rule_id]}
              </span>
              <span className="finding-sev">Mức {h.severity.toLowerCase()}</span>
              <p>{h.message}</p>
              {d.transactions.length > 1 && (
                <span className="sub">
                  Giao dịch {h.txn_id}
                  {n > 1 && ` và ${n - 1} giao dịch khác cùng loại trong cụm`}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      {a?.anomaly.burst_day && (
        <div className="signal">
          <span className="finding-code">Mô hình cụm mua dồn</span>
          <p>{a.anomaly.burst_day.description}</p>
        </div>
      )}
      {a && a.anomaly.reasons.length > 0 && (
        <div className="signal">
          <span className="finding-code">Mô hình bất thường (so với thói quen chi tiêu)</span>
          <ul>
            {a.anomaly.reasons.map((r) => <li key={r}>{r}</li>)}
          </ul>
        </div>
      )}
      {a && (
        <dl className="minor">
          <div>
            <dt>Nhóm chi phí</dt>
            <dd>
              {a.expense_group.name}
              {a.expense_group.source === 'model' && (
                <span className="muted"> (mô hình gợi ý, độ tin cậy {Math.round(a.expense_group.confidence * 100)}%)</span>
              )}
            </dd>
          </div>
          {a.budget && (
            <div>
              <dt>Ngân sách {a.budget.group_name.toLowerCase()} tháng này</dt>
              <dd className={a.budget.remaining < 0 ? 'over' : ''}>
                Đã chi {money(a.budget.spent)} / {money(a.budget.budget)} ({Math.round(a.budget.ratio * 100)}%)
                {a.budget.remaining < 0 ? `, vượt ${money(-a.budget.remaining)}` : `, còn ${money(a.budget.remaining)}`}
              </dd>
            </div>
          )}
        </dl>
      )}
      {!a && <p className="muted">Phân tích chi tiết sẽ hiện khi máy chủ nạp xong mô hình.</p>}
    </Section>
  )
}

// ---------------------------------------------------------------- giải thích của AI, trích dẫn ở lề
function AiMemo({ d }: { d: Detail }) {
  const qc = useQueryClient()
  const { user } = useUser()
  const latest = d.explanations.find((e) => e.kind === 'giai_thich')
  const mut = useMutation({
    mutationFn: (force: boolean) => api.explain(d.case.id, user.id, force),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['case', d.case.id] }),
  })
  const e: Explanation | undefined = mut.data ?? latest
  return (
    <Section
      title="Giải thích của AI"
      aside={
        <button type="button" className="btn-quiet" disabled={mut.isPending} onClick={() => mut.mutate(Boolean(e))}>
          {mut.isPending ? 'Đang tạo giải thích…' : e ? 'Tạo lại giải thích' : 'Tạo giải thích'}
        </button>
      }
    >
      {mut.error && <ErrorNote error={mut.error} />}
      {!e && !mut.isPending && (
        <p className="empty">
          Chưa có giải thích. AI sẽ đọc kết quả ở trên cùng các điều quy chế liên quan, rồi nêu lý do, thông tin cần kiểm tra và
          bước xử lý. AI không thay đổi mức rủi ro.
        </p>
      )}
      {e && (
        <div className="memo">
          <div className="memo-body">
            <p className="memo-summary">{e.explanation.summary}</p>
            <h3>Lý do</h3>
            <ul>
              {e.explanation.reasons.map((r, i) => (
                <li key={i}>
                  <span className="src">
                    {SOURCE_LABEL[r.source]}
                    {r.ref && ` ${r.ref}`}
                  </span>
                  {r.text}
                </li>
              ))}
            </ul>
            <h3>Cần kiểm tra</h3>
            <ul className="checklist">
              {e.explanation.items_to_verify.map((x, i) => <li key={i}>{x}</li>)}
            </ul>
            <h3>Bước tiếp theo</h3>
            <ol>
              {e.explanation.next_steps.map((x, i) => <li key={i}>{x}</li>)}
            </ol>
          </div>
          <aside className="memo-margin" aria-label="Căn cứ quy chế">
            {e.explanation.citations.map((c, i) => (
              <figure key={i} className="note">
                <figcaption>
                  Điều {Number(c.article_id.slice(1))}
                  {e.articles?.[c.article_id] && <span>{e.articles[c.article_id].title.replace(/^Điều \d+\.\s*/, '')}</span>}
                </figcaption>
                <blockquote>{c.quote}</blockquote>
              </figure>
            ))}
            {e.explanation.citations.length === 0 && <p className="muted">Không có trích dẫn hợp lệ.</p>}
          </aside>
          <footer className="memo-foot">
            <span>
              {e.model}, {e.latency_s.toLocaleString('vi-VN')} giây
            </span>
            {e.warnings.length > 0 && (
              <details>
                <summary>{e.warnings.length} cảnh báo kiểm chứng</summary>
                <ul>
                  {e.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              </details>
            )}
          </footer>
        </div>
      )}
    </Section>
  )
}

// ---------------------------------------------------------------- giao dịch
function Transactions({ d, meta }: { d: Detail; meta: Meta }) {
  return (
    <Section title={d.transactions.length > 1 ? `Giao dịch trong hồ sơ (${d.transactions.length})` : 'Giao dịch'}>
      <div className="scroll-x">
        <table className="ledger compact">
          <thead>
            <tr>
              <th scope="col">Mã</th>
              <th scope="col">Ngày mua</th>
              <th scope="col">Cửa hàng và mặt hàng</th>
              <th scope="col">Nhóm chi phí</th>
              <th scope="col" className="r">
                Số tiền
              </th>
              <th scope="col">Quy tắc</th>
            </tr>
          </thead>
          <tbody>
            {d.transactions.map((t) => (
              <tr key={t.txn_id}>
                <td className="nowrap">{t.txn_id}</td>
                <td className="nowrap">{fmtDate(t.tx_date)}</td>
                <td className="case-cell">
                  {t.merchant}
                  <span className="sub">{t.item_desc}</span>
                </td>
                <td>{meta.groups[t.expense_group] ?? t.expense_group}</td>
                <td className="r num">{money(t.amount)}</td>
                <td>
                  <RuleCodes codes={[...new Set(t.rule_hits.map((h) => h.rule_id))]} names={meta.rules} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  )
}

// ---------------------------------------------------------------- xử lý hồ sơ
function ActionPanel({ d, meta }: { d: Detail; meta: Meta }) {
  const qc = useQueryClient()
  const { user } = useUser()
  const [action, setAction] = useState<string | null>(null)
  const [note, setNote] = useState('')
  const [feedback, setFeedback] = useState<Record<string, string>>({})
  const signals = [...d.case.rules, ...(d.case.has_anomaly ? ['mo_hinh'] : [])]
  const mut = useMutation({
    mutationFn: () => api.action(d.case.id, { action: action!, user: user.id, role: user.role, note, signal_feedback: feedback }),
    onSuccess: () => {
      setAction(null)
      setNote('')
      setFeedback({})
      qc.invalidateQueries({ queryKey: ['case', d.case.id] })
      qc.invalidateQueries({ queryKey: ['cases'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
  })
  const asksFeedback = action === 'chap_nhan' || action === 'chuyen_phe_duyet' || action === 'phe_duyet' || action === 'tu_choi'

  return (
    <section className="panel">
      <h2>Xử lý hồ sơ</h2>
      {d.allowed_actions.length === 0 && (
        <p className="muted">
          {user.role === 'ke_toan' && d.case.status === 'Chờ phê duyệt'
            ? 'Hồ sơ đang chờ quản lý phê duyệt.'
            : 'Vai trò hiện tại không có thao tác nào ở trạng thái này.'}
        </p>
      )}
      {d.case.level === 'Cảnh báo cao' && d.case.status === 'Đang review' && user.role === 'ke_toan' && (
        <p className="hint">Hồ sơ cảnh báo cao phải chuyển quản lý phê duyệt (Điều 6 quy chế).</p>
      )}
      <div className="actions">
        {d.allowed_actions.map((a) => (
          <button
            key={a}
            type="button"
            className={action === a ? 'btn-primary' : 'btn'}
            aria-pressed={action === a}
            onClick={() => setAction(action === a ? null : a)}
          >
            {meta.actions[a].label}
          </button>
        ))}
      </div>
      {action && (
        <form
          className="action-form"
          onSubmit={(e) => {
            e.preventDefault()
            mut.mutate()
          }}
        >
          {asksFeedback && signals.length > 0 && (
            <fieldset>
              <legend>Cảnh báo nào là đúng?</legend>
              <p className="hint">Đánh giá này giúp hệ thống giảm cảnh báo giả về sau.</p>
              {signals.map((s) => (
                <div key={s} className="verdict">
                  <span>{s === 'mo_hinh' ? 'Dấu hiệu từ mô hình' : `${s} ${meta.rules[s]}`}</span>
                  {Object.entries(meta.signal_verdicts).map(([k, label]) => (
                    <label key={k}>
                      <input
                        type="radio"
                        name={`fb-${s}`}
                        checked={feedback[s] === k}
                        onChange={() => setFeedback({ ...feedback, [s]: k })}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              ))}
            </fieldset>
          )}
          <label className="block">
            Ghi chú{action === 'tu_choi' ? ' (bắt buộc: lý do từ chối)' : ''}
            <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} required={action === 'tu_choi'} />
          </label>
          {mut.error && <ErrorNote error={mut.error} />}
          <button type="submit" className="btn-primary" disabled={mut.isPending}>
            {mut.isPending ? 'Đang lưu…' : meta.actions[action].label}
          </button>
        </form>
      )}
    </section>
  )
}

function History({ d, meta }: { d: Detail; meta: Meta }) {
  if (!d.decisions.length && !d.review_requests.length) return null
  return (
    <section className="panel">
      <h2>Lịch sử xử lý</h2>
      <ol className="timeline">
        {d.decisions.map((x, i) => (
          <li key={i}>
            <strong>{meta.actions[x.action]?.label ?? x.action}</strong>
            <span className="sub">
              {x.user}, {new Date(x.created_at).toLocaleString('vi-VN')}
            </span>
            <span className="sub">
              {x.from} sang {x.to}
              {x.conclusion && `; kết luận: ${meta.conclusions[x.conclusion]}`}
            </span>
            {Object.keys(x.signal_feedback).length > 0 && (
              <span className="sub">
                {Object.entries(x.signal_feedback)
                  .map(([k, v]) => `${k === 'mo_hinh' ? 'Mô hình' : k}: ${meta.signal_verdicts[v].toLowerCase()}`)
                  .join('; ')}
              </span>
            )}
            {x.note && <p>{x.note}</p>}
          </li>
        ))}
      </ol>
      {d.review_requests.length > 0 && (
        <>
          <h3>Yêu cầu review</h3>
          <ul className="plain">
            {d.review_requests.map((r) => (
              <li key={r.id}>
                {r.reason} <span className="sub">Gửi {r.route_to === 'quan_ly' ? 'quản lý' : 'kế toán'}, {r.status}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
