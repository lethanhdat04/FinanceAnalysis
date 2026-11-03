import { useMutation } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { api, type ChatReply } from '../api'
import { ErrorNote } from '../components'
import { useUser } from '../user'

const TOOL_LABEL: Record<string, string> = {
  lookup_transaction: 'Tra cứu hồ sơ giao dịch',
  get_cardholder_history: 'Xem lịch sử người chi',
  check_budget: 'Kiểm tra ngân sách',
  search_policy: 'Tìm trong quy chế',
  propose_review_request: 'Soạn đề xuất yêu cầu review',
}

const EXAMPLES = [
  'Giao dịch 202608-020997 bị cảnh báo vì sao?',
  'Mua hàng trên Amazon thì xác định loại chi phí thế nào theo quy chế?',
  'Người chi của giao dịch 202605-010177 thường mua gì?',
]

type Turn = { q: string; reply?: ChatReply; error?: unknown }

export default function Assistant() {
  const { user } = useUser()
  const session = useRef(crypto.randomUUID())
  const [turns, setTurns] = useState<Turn[]>([])
  const [text, setText] = useState('')
  const [confirmed, setConfirmed] = useState<Record<string, number>>({})

  const ask = useMutation({
    mutationFn: (q: string) => api.chat(session.current, q, user.id),
    onMutate: (q) => setTurns((t) => [...t, { q }]),
    onSuccess: (reply) => setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, reply } : x))),
    onError: (error) => setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error } : x))),
  })
  const confirm = useMutation({
    mutationFn: (pid: string) => api.confirmProposal(pid, session.current, user.id),
    onSuccess: (r, pid) => setConfirmed((c) => ({ ...c, [pid]: r.id })),
  })

  const submit = (q: string) => {
    if (!q.trim() || ask.isPending) return
    ask.mutate(q.trim())
    setText('')
  }

  return (
    <>
      <header className="page-head">
        <h1>Trợ lý tra cứu</h1>
        <p className="lede">
          Hỏi về giao dịch, người chi, ngân sách hoặc quy chế. Số liệu trong câu trả lời được lấy từ hệ thống; trợ lý chỉ đề xuất
          yêu cầu review, bạn xác nhận mới được tạo.
        </p>
      </header>

      <div className="chat">
        {turns.length === 0 && (
          <div className="examples">
            <p>Thử hỏi:</p>
            {EXAMPLES.map((e) => (
              <button key={e} type="button" className="btn-quiet" onClick={() => submit(e)}>
                {e}
              </button>
            ))}
          </div>
        )}
        {turns.map((t, i) => (
          <article key={i} className="turn">
            <p className="q">{t.q}</p>
            {!t.reply && !t.error && <p className="muted">Đang tra cứu…</p>}
            {t.error !== undefined && <ErrorNote error={t.error} />}
            {t.reply && (
              <div className="a">
                {t.reply.tool_calls.length > 0 && (
                  <p className="trace">
                    Đã dùng: {[...new Set(t.reply.tool_calls.map((c) => TOOL_LABEL[c.tool] ?? c.tool))].join(', ')}
                  </p>
                )}
                <Markdown>{t.reply.answer}</Markdown>
                {t.reply.pending_reviews.map((p) => (
                  <div key={p.proposal_id} className="proposal">
                    <h3>Đề xuất yêu cầu review</h3>
                    <dl className="minor">
                      <div>
                        <dt>Giao dịch</dt>
                        <dd>{p.txn_id}</dd>
                      </div>
                      <div>
                        <dt>Gửi tới</dt>
                        <dd>{p.route_to === 'quan_ly' ? 'Quản lý' : 'Kế toán'}</dd>
                      </div>
                      <div>
                        <dt>Lý do</dt>
                        <dd>{p.reason}</dd>
                      </div>
                      <div>
                        <dt>Chứng từ cần bổ sung</dt>
                        <dd>{p.required_documents.join(', ') || 'Không'}</dd>
                      </div>
                    </dl>
                    {confirmed[p.proposal_id] ? (
                      <p className="ok">Đã tạo yêu cầu review số {confirmed[p.proposal_id]}.</p>
                    ) : (
                      <button
                        type="button"
                        className="btn-primary"
                        disabled={confirm.isPending}
                        onClick={() => confirm.mutate(p.proposal_id)}
                      >
                        Tạo yêu cầu review
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </article>
        ))}
        {confirm.error && <ErrorNote error={confirm.error} />}
      </div>

      <form
        className="ask"
        onSubmit={(e) => {
          e.preventDefault()
          submit(text)
        }}
      >
        <label className="sr-only" htmlFor="ask-input">
          Câu hỏi
        </label>
        <textarea
          id="ask-input"
          rows={2}
          value={text}
          placeholder="Ví dụ: ngân sách văn phòng phẩm của đơn vị 01000 tháng 8/2026 còn bao nhiêu?"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit(text)
            }
          }}
        />
        <button type="submit" className="btn-primary" disabled={ask.isPending || !text.trim()}>
          Gửi câu hỏi
        </button>
      </form>
    </>
  )
}
