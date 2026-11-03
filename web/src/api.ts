// Kiểu dữ liệu và hàm gọi API backend (app/main.py)

export type Level = 'Cảnh báo cao' | 'Cần review'
export type Role = 'ke_toan' | 'quan_ly'

export interface CaseRow {
  id: string
  kind: 'giao_dich' | 'cum_mua_don'
  cardholder_id: string
  agency: string
  tx_date: string
  month: string
  n_txn: number
  total_amount: number
  level: Level
  rules: string[]
  has_anomaly: boolean
  title: string
  status: string
  assignee: string | null
}

export interface RuleHit {
  rule_id: string
  rule_name: string
  severity: 'Cao' | 'Trung bình' | 'Thấp'
  message: string
  related_txn_ids: string
}

export interface CaseTxn {
  txn_id: string
  tx_date: string
  post_date: string
  merchant: string
  item_desc: string
  amount: number
  mcc_desc: string
  expense_group: string
  final_level: string
  anomaly_tx_flag: boolean
  rule_hits: RuleHit[]
}

export interface Analysis {
  txn_id: string
  expense_group: { group: string; name: string; source: 'mcc' | 'model'; confidence: number }
  anomaly: {
    score: number | null
    flag: boolean
    reasons: string[]
    signals: string[]
    burst_day: { description: string; txn_ids: string[] } | null
  }
  budget: { budget: number; spent: number; ratio: number; remaining: number; group_name: string; month: string } | null
  risk: { rule_level: string; final_level: string }
}

export interface Explanation {
  id?: number
  kind?: 'giai_thich' | 'dieu_tra'
  cached?: boolean
  model: string
  latency_s: number
  warnings: string[]
  created_at?: string
  articles?: Record<string, { title: string; text: string }>
  explanation: {
    summary: string
    reasons: { text: string; source: 'rule' | 'mo_hinh' | 'phan_loai' | 'quy_che'; ref: string }[]
    citations: { article_id: string; quote: string }[]
    items_to_verify: string[]
    next_steps: string[]
  }
}

export interface DecisionRow {
  user: string
  role: Role
  action: string
  from: string
  to: string
  conclusion: string | null
  signal_feedback: Record<string, string>
  note: string
  created_at: string
}

export interface ReviewRequestRow {
  id: number
  case_id?: string | null
  txn_id: string
  reason: string
  route_to: string
  required_documents: string[]
  status: string
  source: string
  created_by: string
  created_at?: string
}

export interface CaseDetail {
  case: CaseRow
  allowed_actions: string[]
  transactions: CaseTxn[]
  analysis: Analysis | null
  explanations: Explanation[]
  decisions: DecisionRow[]
  review_requests: ReviewRequestRow[]
}

export interface Meta {
  statuses: string[]
  roles: Record<Role, string>
  rules: Record<string, string>
  groups: Record<string, string>
  months: string[]
  conclusions: Record<string, string>
  signal_verdicts: Record<string, string>
  actions: Record<string, { label: string; role: Role }>
}

export interface Stats {
  transactions: number
  cases: number
  by_level: Record<Level, number>
  by_status: Record<string, number>
  by_kind: Record<string, number>
}

export interface ChatReply {
  answer: string
  tool_calls: { tool: string; args: Record<string, unknown> }[]
  pending_reviews: (ReviewRequestRow & { proposal_id: string })[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let msg = `Lỗi ${res.status}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') msg = body.detail
    } catch {
      /* giữ thông báo mặc định */
    }
    throw new ApiError(res.status, msg)
  }
  return res.json()
}

const enc = encodeURIComponent

export const api = {
  meta: () => request<Meta>('/api/meta'),
  stats: (month?: string) => request<Stats>(`/api/stats${month ? `?month=${enc(month)}` : ''}`),
  cases: (params: Record<string, string>) =>
    request<{ total: number; page: number; size: number; items: CaseRow[] }>(
      `/api/cases?${new URLSearchParams(params)}`,
    ),
  caseDetail: (id: string, role: Role) => request<CaseDetail>(`/api/cases/${enc(id)}/detail?role=${role}`),
  explain: (id: string, user: string, force = false) =>
    request<Explanation>(`/api/cases/${enc(id)}/explain`, { method: 'POST', body: JSON.stringify({ user, force }) }),
  action: (id: string, body: { action: string; user: string; role: Role; note: string; signal_feedback: Record<string, string> }) =>
    request<{ case: CaseRow; allowed_actions: string[] }>(`/api/cases/${enc(id)}/actions`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  chat: (session_id: string, message: string, user: string) =>
    request<ChatReply>('/api/chat', { method: 'POST', body: JSON.stringify({ session_id, message, user }) }),
  confirmProposal: (proposalId: string, session_id: string, user: string) =>
    request<ReviewRequestRow>(`/api/review-proposals/${enc(proposalId)}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ session_id, user }),
    }),
  reviewRequests: () => request<ReviewRequestRow[]>('/api/review-requests'),
}

// ---------------------------------------------------------------- định dạng
const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
export const money = (x: number) => usd.format(x)
export const fmtDate = (iso: string) => {
  const [y, m, d] = iso.slice(0, 10).split('-')
  return `${d}/${m}/${y}`
}
export const fmtMonth = (ym: string) => {
  const [y, m] = ym.split('-')
  return `Tháng ${Number(m)}/${y}`
}
export const caseUrl = (id: string) => `/ho-so/${enc(id)}`
