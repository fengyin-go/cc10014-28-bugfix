import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/log'
const PAGE_SIZE = 20
const columns = [
  "记录编号",
  "调用服务",
  "请求时间",
  "响应耗时",
  "返回码",
  "调用方IP",
  "请求大小KB",
  "响应大小KB"
]
const actions = [
  "归档记录",
  "清理日志",
  "导出日志"
]
const formFields = [
  "记录编号",
  "调用服务",
  "请求时间",
  "响应耗时",
  "返回码",
  "调用方IP",
  "请求大小KB",
  "响应大小KB"
]

type Row = {
  id: number
  [key: string]: unknown
}

type ContractStats = {
  total: number
  abnormal: number
  error_rate: number
  by_status_code: { 返回码: string; 调用数: number; 异常数: number }[]
}

const emptyForm: Record<string, string> = Object.fromEntries(formFields.map((field) => [field, '']))

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

export default function LogView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [stats, setStats] = useState<ContractStats | null>(null)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [draft, setDraft] = useState<Record<string, string>>({ 记录编号: '', 返回码: '', 请求时间: '' })
  const [applied, setApplied] = useState<Record<string, string>>({ 记录编号: '', 返回码: '', 请求时间: '' })
  const [abnormalOnly, setAbnormalOnly] = useState(false)
  const [detail, setDetail] = useState<Row | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<Record<string, string>>(emptyForm)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const params = new URLSearchParams()
    if (applied.记录编号) params.set('keyword', applied.记录编号)
    if (applied.返回码) params.set('返回码', applied.返回码)
    if (applied.请求时间) params.set('请求时间', applied.请求时间)
    if (abnormalOnly) params.set('abnormal', 'true')
    params.set('page', String(page))
    params.set('size', String(PAGE_SIZE))
    try {
      const [listResponse, statsResponse] = await Promise.all([
        request(`${ENDPOINT}?${params.toString()}`),
        request(`${ENDPOINT}/contract/stats`),
      ])
      if (!listResponse.ok) {
        throw new Error('日志记录列表读取失败')
      }
      const payload = await listResponse.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      if (statsResponse.ok) {
        setStats((await statsResponse.json()) as ContractStats)
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '调用日志列表读取失败')
    }
  }, [applied, abnormalOnly, page])

  useEffect(() => {
    void reload()
  }, [reload])

  function applyFilters(event: React.FormEvent) {
    event.preventDefault()
    setPage(1)
    setApplied({ ...draft })
  }

  function resetFilters() {
    setDraft({ 记录编号: '', 返回码: '', 请求时间: '' })
    setApplied({ 记录编号: '', 返回码: '', 请求时间: '' })
    setAbnormalOnly(false)
    setPage(1)
  }

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      const result = await response.json()
      if (!response.ok || !result.ok) {
        throw new Error(result.message ?? '调用日志动作未生效，请稍后重试')
      }
      setNotice(result.message ?? '动作已执行')
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '调用日志操作失败')
    }
  }

  async function submitRegistration(event: React.FormEvent) {
    event.preventDefault()
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: form }),
      })
      const result = await response.json()
      if (!response.ok || !result.ok) {
        throw new Error(result.message ?? '日志记录登记失败')
      }
      setNotice(result.message ?? '日志记录已登记')
      setForm(emptyForm)
      setShowForm(false)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '日志记录登记失败')
    }
  }

  async function openDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}`)
      if (!response.ok) {
        throw new Error('日志记录详情读取失败')
      }
      setDetail((await response.json()) as Row)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '日志记录详情读取失败')
    }
  }

  const statCards = [
    { label: '日志记录总数', value: stats?.total ?? 0 },
    { label: '异常记录数', value: stats?.abnormal ?? 0 },
    { label: '错误率', value: `${(((stats?.error_rate ?? 0) * 100)).toFixed(2)}%` },
  ]

  return (
    <section className="page" data-module="log">
      <header className="page-head">
        <div>
          <h2>调用日志管理</h2>
          <p className="page-desc">维护调用日志记录的输入、执行约束与结果记录，为调用日志提供可验证的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setShowForm((prev) => !prev)}>
            登记日志记录
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出调用日志清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {statCards.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      {stats && stats.by_status_code.length > 0 && (
        <table className="data-table stats-breakdown">
          <thead>
            <tr><th>返回码</th><th>调用数</th><th>异常数</th></tr>
          </thead>
          <tbody>
            {stats.by_status_code.map((bucket) => (
              <tr key={bucket.返回码}>
                <td>{bucket.返回码}</td>
                <td>{bucket.调用数}</td>
                <td>{bucket.异常数}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {showForm && (
        <form className="form-panel" onSubmit={submitRegistration}>
          <div className="form-grid">
            {formFields.map((field) => (
              <label key={field} className="filter-item">
                <span>{field}</span>
                <input
                  value={form[field] ?? ''}
                  placeholder={`请输入${field}`}
                  onChange={(event) => setForm((prev) => ({ ...prev, [field]: event.target.value }))}
                />
              </label>
            ))}
          </div>
          <div className="form-actions">
            <button className="btn primary" type="submit">提交登记</button>
            <button className="btn ghost" type="button" onClick={() => { setShowForm(false); setForm(emptyForm) }}>取消</button>
          </div>
        </form>
      )}

      <form className="filter-bar" onSubmit={applyFilters}>
        <label className="filter-item">
          <span>记录编号</span>
          <input
            value={draft.记录编号}
            placeholder="按记录编号检索"
            onChange={(event) => setDraft((prev) => ({ ...prev, 记录编号: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>返回码</span>
          <input
            value={draft.返回码}
            placeholder="按返回码检索"
            onChange={(event) => setDraft((prev) => ({ ...prev, 返回码: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>请求时间</span>
          <input
            value={draft.请求时间}
            placeholder="按请求时间筛选"
            onChange={(event) => setDraft((prev) => ({ ...prev, 请求时间: event.target.value }))}
          />
        </label>
        <label className="filter-item filter-check">
          <input
            type="checkbox"
            checked={abnormalOnly}
            onChange={(event) => { setAbnormalOnly(event.target.checked); setPage(1) }}
          />
          <span>仅看异常记录</span>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={resetFilters}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{formatCell(row[column])}</td>)}
              <td className="row-actions">
                <button className="link" type="button" onClick={() => void openDetail(row)}>
                  详情
                </button>
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无调用日志数据，可先登记日志记录</td>
            </tr>
          )}
        </tbody>
      </table>

      <div className="pagination">
        <button className="btn" type="button" disabled={page <= 1} onClick={() => setPage((prev) => Math.max(prev - 1, 1))}>
          上一页
        </button>
        <span>第 {page} 页</span>
        <button
          className="btn"
          type="button"
          disabled={page * PAGE_SIZE >= total}
          onClick={() => setPage((prev) => prev + 1)}
        >
          下一页
        </button>
      </div>

      {detail && (
        <section className="detail-panel">
          <header className="detail-head">
            <h3>日志记录详情：{formatCell(detail.记录编号)}</h3>
            <button className="btn ghost" type="button" onClick={() => setDetail(null)}>关闭</button>
          </header>
          <dl className="detail-grid">
            {columns.map((column) => (
              <div key={column} className="detail-item">
                <dt>{column}</dt>
                <dd>{formatCell(detail[column])}</dd>
              </div>
            ))}
            <div className="detail-item">
              <dt>状态</dt>
              <dd>{formatCell(detail.status)}</dd>
            </div>
            <div className="detail-item">
              <dt>异常标记</dt>
              <dd>{detail.abnormal ? '异常' : '正常'}</dd>
            </div>
            <div className="detail-item">
              <dt>解析版本</dt>
              <dd>{formatCell(detail.解析版本)}</dd>
            </div>
          </dl>
          {Array.isArray(detail.解析依据) && (
            <div className="basis-block">
              <h4>解析依据</h4>
              <ul className="basis-list">
                {(detail.解析依据 as unknown[]).map((item, index) => (
                  <li key={index}>{String(item)}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <footer className="page-foot">
        <span>共 {total} 条调用日志记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
