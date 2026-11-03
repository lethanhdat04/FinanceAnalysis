import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type CalibrationProposal, type CalibrationRecord, type CalibrationState } from '../api'
import { ErrorNote, Section } from '../components'
import { useUser } from '../user'

const pct = (x: number) => `${Math.round(x * 100)}%`
const STATUS = { dang_ap_dung: 'Đang áp dụng', tu_choi: 'Đã từ chối', da_go: 'Đã gỡ' } as const

export default function Calibration() {
  const q = useQuery({ queryKey: ['calibration'], queryFn: api.calibration })
  const { user } = useUser()
  const manager = user.role === 'quan_ly'
  const st = q.data
  const [flash, setFlash] = useState<string | null>(null)

  return (
    <>
      <header className="page-head">
        <h1>Hiệu chỉnh cảnh báo</h1>
        <p className="lede">
          Mỗi khi xử lý hồ sơ, kế toán đánh giá từng tín hiệu là đúng hay cảnh báo giả. Khi một loại tín hiệu, trong một
          ngữ cảnh cụ thể, chắc chắn hiếm khi đúng, hệ thống đề xuất hạ nó xuống mức ghi chú. Đề xuất chỉ có hiệu lực khi
          quản lý áp dụng; lúc đó các hồ sơ còn chờ review được tính lại mức.
        </p>
      </header>
      {q.error && <ErrorNote error={q.error} />}
      {q.isPending && <p className="empty">Đang tổng hợp phản hồi…</p>}
      {st && (
        <>
          {st.n_simulated > 0 && (
            <p className="notice">
              {st.n_simulated.toLocaleString('vi-VN')} trong {st.n_feedback.toLocaleString('vi-VN')} đánh giá là phản hồi
              mô phỏng (scripts/simulate_feedback.py), dùng để thử cơ chế này. Đây không phải kết luận thật của kế toán.
            </p>
          )}
          {flash && <p className="ok" role="status">{flash}</p>}
          {!manager && (
            <p className="hint">Bạn đang xem với vai trò kế toán. Chỉ quản lý được áp dụng, từ chối hoặc gỡ hiệu chỉnh.</p>
          )}
          <Summary st={st} />
          <Section title={`Đề xuất mới (${st.proposals.length})`}>
            {st.proposals.length === 0 ? (
              <p className="empty">
                Chưa có ngữ cảnh nào đủ chắc chắn. Mỗi ngữ cảnh cần ít nhất {st.settings.min_n} đánh giá, và tỷ lệ đúng phải chắc
                chắn ({pct(st.settings.confidence)}) thấp hơn {pct(st.settings.tau)}.
              </p>
            ) : (
              <ul className="calib-list">
                {st.proposals.map((p) => (
                  <ProposalItem key={p.key} p={p} tau={st.settings.tau} manager={manager} onDone={setFlash} />
                ))}
              </ul>
            )}
          </Section>
          <Section title={`Đang áp dụng (${st.active.length})`}>
            {st.active.length === 0 ? (
              <p className="empty">Chưa áp dụng hiệu chỉnh nào; mọi tín hiệu đang ở mức gốc của quy tắc và mô hình.</p>
            ) : (
              <ul className="calib-list">
                {st.active.map((c) => (
                  <ActiveItem key={c.key} c={c} tau={st.settings.tau} manager={manager} onDone={setFlash} />
                ))}
              </ul>
            )}
          </Section>
          {st.history.length > 0 && (
            <Section title="Đã quyết định trước đây">
              <ul className="plain">
                {st.history.map((c) => (
                  <li key={c.key}>
                    {c.text}
                    <span className="sub">
                      {STATUS[c.status]} bởi {c.decided_by}
                      {c.decided_at && `, ${new Date(c.decided_at).toLocaleString('vi-VN')}`}
                      {c.note && `. Ghi chú: ${c.note}`}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          <p className="hint">
            Tham số (config/rules.toml): ngưỡng τ = {pct(st.settings.tau)}, tức chấp nhận bỏ sót 1 vấn đề thật để bớt khoảng{' '}
            {Math.round((1 - st.settings.tau) / st.settings.tau)} lần review thừa; giả định kế toán đánh giá nhầm{' '}
            {pct(st.settings.noise)}; đề xuất đang áp dụng được gợi ý gỡ khi độ chắc chắn giảm dưới{' '}
            {pct(st.settings.revoke_below)}.
          </p>
        </>
      )}
    </>
  )
}

function Summary({ st }: { st: CalibrationState }) {
  return (
    <Section title="Phản hồi theo tín hiệu">
      <table className="ledger compact calib-summary">
        <thead>
          <tr>
            <th scope="col">Tín hiệu</th>
            <th scope="col" className="r">Số đánh giá</th>
            <th scope="col" className="r">Đúng</th>
            <th scope="col">Tỷ lệ được xác nhận đúng</th>
          </tr>
        </thead>
        <tbody>
          {st.summary.map((x) => (
            <tr key={x.signal}>
              <td>
                <strong>{x.signal === 'mo_hinh' || x.signal === 'cum_mua_don' ? 'Mô hình' : x.signal}</strong> {x.name}
                {x.protected && <span className="sub">Quy định cứng của quy chế, phản hồi không làm thay đổi</span>}
              </td>
              <td className="r num">{x.n.toLocaleString('vi-VN')}</td>
              <td className="r num">{x.n ? pct(x.n_true / x.n) : ''}</td>
              <td>{x.n > 0 && <RateBar rate={x.n_true / x.n} tau={x.protected ? null : st.settings.tau} />}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Section>
  )
}

/** Thước tỷ lệ đúng; vạch đỏ là ngưỡng τ dưới đó một ngữ cảnh có thể được hạ mức. */
function RateBar({ rate, tau }: { rate: number; tau: number | null }) {
  return (
    <span className="ratebar" role="img" aria-label={`Tỷ lệ đúng ${pct(rate)}${tau !== null ? `, ngưỡng ${pct(tau)}` : ''}`}>
      <span className="ratebar-fill" style={{ width: pct(rate) }} />
      {tau !== null && <span className="ratebar-tau" style={{ left: pct(tau) }} />}
    </span>
  )
}

const DONE = { ap_dung: 'Đã áp dụng hiệu chỉnh', tu_choi: 'Đã từ chối đề xuất', go_bo: 'Đã gỡ hiệu chỉnh' } as const

function useDecide(onDone: (msg: string) => void) {
  const qc = useQueryClient()
  const { user } = useUser()
  return useMutation({
    mutationFn: (v: { key: string; decision: 'ap_dung' | 'tu_choi' | 'go_bo'; note: string }) =>
      api.decideCalibration({ ...v, user: user.id, role: user.role }),
    onSuccess: (r, v) => {
      onDone(
        v.decision === 'tu_choi'
          ? `${DONE[v.decision]}.`
          : `${DONE[v.decision]}; ${r.cases_changed.toLocaleString('vi-VN')} hồ sơ chờ review được tính lại mức.`,
      )
      qc.invalidateQueries({ queryKey: ['calibration'] })
      qc.invalidateQueries({ queryKey: ['cases'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['case'] })
    },
  })
}

function Evidence({ p, tau }: { p: CalibrationProposal; tau: number }) {
  return (
    <p className="calib-evidence">
      <RateBar rate={p.n_true / p.n} tau={tau} />
      <span>
        {p.n_true}/{p.n} được xác nhận đúng; hiện có {p.affected.toLocaleString('vi-VN')} tín hiệu cùng ngữ cảnh trong hồ
        sơ chờ review.
      </span>
    </p>
  )
}

type ItemProps = { tau: number; manager: boolean; onDone: (msg: string) => void }

function ProposalItem({ p, tau, manager, onDone }: ItemProps & { p: CalibrationProposal }) {
  const decide = useDecide(onDone)
  const [note, setNote] = useState('')
  return (
    <li>
      <p className="calib-text">{p.title}</p>
      <Evidence p={p} tau={tau} />
      {p.previous && (
        <p className="sub">
          Đã {p.previous.status === 'tu_choi' ? 'bị từ chối' : 'được gỡ'} trước đây bởi {p.previous.decided_by}
          {p.previous.n != null && ` khi có ${p.previous.n} phản hồi`}
          {p.previous.note && ` (${p.previous.note})`}; được đề xuất lại vì có thêm phản hồi.
        </p>
      )}
      {manager && (
        <div className="calib-act">
          <input
            aria-label="Ghi chú quyết định"
            placeholder="Ghi chú (không bắt buộc)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <button type="button" className="btn-primary" disabled={decide.isPending}
            onClick={() => decide.mutate({ key: p.key, decision: 'ap_dung', note })}>
            Áp dụng
          </button>
          <button type="button" className="btn" disabled={decide.isPending}
            onClick={() => decide.mutate({ key: p.key, decision: 'tu_choi', note })}>
            Từ chối
          </button>
        </div>
      )}
      {decide.error && <ErrorNote error={decide.error} />}
    </li>
  )
}

function ActiveItem({ c, tau, manager, onDone }: ItemProps & { c: CalibrationRecord }) {
  const decide = useDecide(onDone)
  const [note, setNote] = useState('')
  return (
    <li className={c.revoke_suggested ? 'calib-warn' : ''}>
      <p className="calib-text">{c.title}</p>
      {c.now ? <Evidence p={c.now} tau={tau} /> : <p className="sub">Chưa đủ phản hồi mới để đánh giá lại.</p>}
      <span className="sub">
        Áp dụng bởi {c.decided_by}
        {c.decided_at && `, ${new Date(c.decided_at).toLocaleString('vi-VN')}`}
        {c.note && `. Ghi chú: ${c.note}`}
      </span>
      {c.revoke_suggested && (
        <p className="over">Phản hồi mới cho thấy ngữ cảnh này không còn chắc chắn hiếm khi đúng. Nên cân nhắc gỡ.</p>
      )}
      {manager && (
        <div className="calib-act">
          <input aria-label="Ghi chú khi gỡ" placeholder="Lý do gỡ (không bắt buộc)" value={note}
            onChange={(e) => setNote(e.target.value)} />
          <button type="button" className="btn" disabled={decide.isPending}
            onClick={() => decide.mutate({ key: c.key, decision: 'go_bo', note })}>
            Gỡ hiệu chỉnh
          </button>
        </div>
      )}
      {decide.error && <ErrorNote error={decide.error} />}
    </li>
  )
}
