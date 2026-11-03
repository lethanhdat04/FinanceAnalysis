import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { agentApi, type AgentJob, type AgentRun, type CaseDetail, type Meta } from '../api'
import { ErrorNote, Section } from '../components'
import { useUser } from '../user'

const ASSESSMENT = {
  dang_lo_ngai: { label: 'Đáng lo ngại', cls: 'as-bad' },
  co_the_giai_thich: { label: 'Có thể giải thích', cls: 'as-ok' },
  chua_du_thong_tin: { label: 'Chưa đủ thông tin', cls: 'as-unknown' },
} as const

const PHASE = {
  lap_ke_hoach: 'Đang lập kế hoạch điều tra',
  dieu_tra: 'Đang thu thập bằng chứng',
  viet_ho_so: 'Đang viết hồ sơ và tự kiểm tra',
  xong: 'Xong',
} as const

export default function AgentPanel({ d, meta }: { d: CaseDetail; meta: Meta }) {
  const qc = useQueryClient()
  const { user } = useUser()
  const [jobId, setJobId] = useState<string | null>(null)
  const saved = d.explanations.find((e) => e.kind === 'dieu_tra') as unknown as AgentRun | undefined

  const start = useMutation({
    mutationFn: () => agentApi.start(d.case.id, user.id, user.role),
    onSuccess: (r) => setJobId(r.job_id),
  })
  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => agentApi.job(jobId!),
    enabled: Boolean(jobId),
    // dừng hỏi khi job xong, lỗi, hoặc không còn trên máy chủ (vd máy chủ khởi động lại thì trả 404)
    refetchInterval: (q) => (q.state.error || q.state.data?.status !== 'dang_chay' ? false : 2500),
    retry: false,
  })
  const finished = Boolean(jobId && job.data && job.data.status !== 'dang_chay')
  const lost = Boolean(jobId && job.error)
  useEffect(() => {
    if (!finished) return
    if (job.data?.status === 'xong') setJobId(null)       // giữ job lỗi để hiện thông báo
    qc.invalidateQueries({ queryKey: ['case', d.case.id] })
  }, [finished, job.data?.status, qc, d.case.id])

  const running = (!lost && job.data?.status === 'dang_chay') || start.isPending
  return (
    <Section
      title="Hồ sơ điều tra của agent"
      aside={
        <button type="button" className="btn-quiet" disabled={running} onClick={() => start.mutate()}>
          {running ? 'Agent đang điều tra…' : saved ? 'Điều tra lại' : 'Cho agent điều tra'}
        </button>
      }
    >
      {start.error && <ErrorNote error={start.error} />}
      {job.data?.status === 'loi' && <ErrorNote error={new Error(job.data.error ?? 'Agent gặp lỗi')} />}
      {lost && (
        <ErrorNote error={new Error('Không theo dõi được phiên điều tra (máy chủ có thể đã khởi động lại). Bấm điều tra lại để chạy phiên mới.')} />
      )}
      {running && job.data && <Progress job={job.data} />}
      {!running && !saved && (
        <p className="empty">
          Agent tự lập kế hoạch, tra cứu giao dịch liên quan, lịch sử người chi, nhà cung cấp, ngân sách, quy chế và kết luận trước
          đây của kế toán, rồi soạn hồ sơ đề xuất. Mỗi lần điều tra mất khoảng 1–3 phút. Agent chỉ đề xuất, không thực hiện thao
          tác nào.
        </p>
      )}
      {!running && saved && <Report run={saved} d={d} meta={meta} />}
    </Section>
  )
}

function Progress({ job }: { job: AgentJob }) {
  return (
    <div className="agent-live" aria-live="polite">
      <p className="phase">{PHASE[job.phase]}</p>
      {job.plan.length > 0 && (
        <ol className="steps">
          {job.plan.map((p, i) => {
            const done = job.trace.some((t) => t.tool === p.tool)
            return (
              <li key={i} className={done ? 'done' : ''}>
                {p.check}
              </li>
            )
          })}
        </ol>
      )}
    </div>
  )
}

function Report({ run, d, meta }: { run: AgentRun; d: CaseDetail; meta: Meta }) {
  const r = run.report
  const { user } = useUser()
  const qc = useQueryClient()
  const create = useMutation({
    mutationFn: () =>
      agentApi.createReview(d.case.id, {
        user: user.id,
        txn_id: run.txn_id,
        reason: r.draft_review_request.reason,
        route_to: r.draft_review_request.route_to,
        required_documents: r.draft_review_request.required_documents,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['case', d.case.id] })
      qc.invalidateQueries({ queryKey: ['review-requests'] })
    },
  })
  const alreadyCreated = d.review_requests.some((x) => x.source === 'agent' && x.reason === r.draft_review_request.reason)
  const action = r.recommended_action === 'khong_de_xuat' ? null : meta.actions[r.recommended_action]?.label

  return (
    <div className="memo">
      <div className="memo-body">
        <p className="memo-summary">{r.summary}</p>
        <h3>Phát hiện</h3>
        <ul className="agent-findings">
          {r.findings.map((f, i) => (
            <li key={i}>
              <span className={`assess ${ASSESSMENT[f.assessment].cls}`}>{ASSESSMENT[f.assessment].label}</span>
              {f.text}
              <span className="evidence">Bằng chứng: {f.evidence.join(', ')}</span>
            </li>
          ))}
        </ul>
        <h3>Cần kiểm tra</h3>
        <ul>
          {r.items_to_verify.map((x, i) => <li key={i}>{x}</li>)}
        </ul>
        <div className="recommend">
          <h3>Agent đề xuất</h3>
          <p>
            <strong>{action ?? 'Chưa đủ căn cứ để đề xuất'}</strong>
            {action && '. '}
            {r.recommendation_reason}
          </p>
          <p className="hint">Bạn quyết định ở phần Xử lý hồ sơ; agent không thực hiện thao tác.</p>
        </div>
        {r.draft_review_request.needed && (
          <div className="proposal">
            <h3>Bản nháp yêu cầu review</h3>
            <p>
              {r.draft_review_request.reason.replace(/[.\s]+$/, '')}. Gửi {r.draft_review_request.route_to === 'quan_ly' ? 'quản lý' : 'kế toán'}; chứng
              từ: {r.draft_review_request.required_documents.join(', ') || 'không'}.
            </p>
            {alreadyCreated || create.isSuccess ? (
              <p className="ok">Đã tạo yêu cầu review.</p>
            ) : (
              <button type="button" className="btn-primary" disabled={create.isPending} onClick={() => create.mutate()}>
                Tạo yêu cầu review theo bản nháp
              </button>
            )}
            {create.error && <ErrorNote error={create.error} />}
          </div>
        )}
      </div>
      <aside className="memo-margin" aria-label="Căn cứ quy chế">
        {r.citations.map((c, i) => (
          <figure key={i} className="note">
            <figcaption>
              Điều {Number(c.article_id.slice(1))}
              {run.articles?.[c.article_id] && <span>{run.articles[c.article_id].title.replace(/^Điều \d+\.\s*/, '')}</span>}
            </figcaption>
            <blockquote>{c.quote}</blockquote>
          </figure>
        ))}
      </aside>
      <footer className="memo-foot">
        <details>
          <summary>
            Các bước agent đã làm: {run.n_tool_calls} lần tra cứu, {run.revisions} lần tự sửa, {run.latency_s.toLocaleString('vi-VN')}{' '}
            giây
          </summary>
          <h3>Kế hoạch</h3>
          <ol>
            {run.plan.map((p, i) => (
              <li key={i}>
                {p.check} <span className="muted">({p.why})</span>
              </li>
            ))}
          </ol>
          <h3>Tra cứu đã thực hiện</h3>
          <ol>
            {run.trace.map((t) => (
              <li key={t.step}>
                {t.label}: {Object.values(t.args).join(', ')}
                {t.error && <span className="over"> (lỗi: {t.error})</span>}
              </li>
            ))}
          </ol>
          <p>
            {run.model}, {run.usage.llm_calls} lượt gọi LLM, {(run.usage.prompt_tokens + run.usage.completion_tokens).toLocaleString('vi-VN')}{' '}
            token
          </p>
          {run.warnings.length > 0 && <p className="over">Cảnh báo kiểm chứng còn lại: {run.warnings.join('; ')}</p>}
        </details>
      </footer>
    </div>
  )
}
