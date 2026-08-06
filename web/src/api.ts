/**
 * Cliente de la API y tipos del contrato.
 *
 * Estos tipos son el espejo de `src/conciliacion/report/contract.py`. Si el
 * contrato cambia, esto tiene que cambiar con él — por eso `Money` no es un
 * `number`.
 *
 * REGLA: el front NO calcula ni formatea plata. Renderiza `formatted`, que
 * viene del backend. Si el front formateara por su cuenta, la web y el CLI
 * podrían mostrar el mismo monto distinto, y ahí el sistema deja de tener una
 * sola versión de la verdad (ADR-0004).
 *
 * `cents` está para comparar y ordenar, nunca para mostrar.
 */

export interface Money {
  cents: number;
  currency: string;
  formatted: string;
}

export interface MovementView {
  id: string;
  ledger_id: string;
  source_id: string;
  external_id: string;
  occurred_on: string;
  occurred_at: string | null;
  amount: Money;
  kind: string;
  status: string;
  description: string;
  reference: string | null;
  counterparty: string | null;
  /** Puntero a la evidencia: `data/raw/...#pagina=2,y=158`. */
  raw_ref: string | null;
  metadata: Record<string, unknown>;
  counts_for_reconciliation: boolean;
}

export interface BreakdownRow {
  kind: string;
  status: string;
  count: number;
  total: Money;
}

export interface LedgerSummary {
  id: string;
  name: string;
  currency: string;
  role: string;
  movement_count: number;
  balance: Money;
  date_range: [string, string] | null;
  breakdown: BreakdownRow[];
  sources: string[];
}

export interface SourceView {
  name: string;
  ledger_id: string;
  connector_id: string;
  adapters: string[];
  requires_network: boolean;
}

export interface SystemView {
  contract_version: string;
  generated_at: string;
  ledgers: LedgerSummary[];
  sources: SourceView[];
  coverage: Record<string, { from: string | null; to: string | null }>;
}

export interface StatementLine {
  position: number;
  movement_id: string;
  occurred_on: string;
  description: string;
  amount: Money;
  /** Saldo que declara el banco. */
  running_balance: Money;
  /** Saldo que calcula el sistema acumulando movimientos. */
  expected_balance: Money;
  chain_ok: boolean;
  page: number | null;
  raw_ref: string | null;
}

export interface StatementView {
  ledger_id: string;
  period: string;
  account_number: string | null;
  opening_balance: Money;
  closing_balance: Money;
  line_count: number;
  chain_intact: boolean;
  lines: StatementLine[];
}

export interface TransactionBreakdown {
  transaction_id: string;
  ledger_id: string;
  occurred_on: string;
  status: string;
  gross: Money | null;
  deductions: MovementView[];
  total_deductions: Money;
  net_expected: Money;
  /** "transaction" | "batch" | null */
  settlement_scope: string | null;
  settlement: Money | null;
  closes_to_zero: boolean | null;
  disbursement_id: string | number | null;
  has_declared_deductions: boolean;
  movements: MovementView[];
}

export interface DisbursementBreakdown {
  disbursement_id: string;
  ledger_id: string;
  settled_on: string;
  settlement: Money;
  transaction_count: number;
  gross_total: Money;
  declared_deductions: Money;
  net_expected: Money;
  residual: Money;
  closes_to_zero: boolean;
  deductions_complete: boolean;
  transaction_ids: string[];
}

export interface Adjustment {
  kind: string;
  amount: Money;
  /** "declared" = el canal lo informó · "inferred" = lo calculó el sistema */
  source: string;
  note: string;
}

export interface Alternative {
  description: string;
  movement_ids: string[];
  rejected_because: string;
  residual: Money | null;
}

export interface Explanation {
  rule_id: string;
  summary: string;
  confidence: string;
  source_movement_ids: string[];
  target_movement_ids: string[];
  gross: Money | null;
  net: Money | null;
  adjustments: Adjustment[];
  adjustments_total: Money | null;
  is_balanced: boolean;
  window: { start: string; end: string; rule: string } | null;
  alternatives: Alternative[];
  unexplained: Money | null;
}

export interface FlowFinding {
  id: string;
  status: string;
  /** `out_of_coverage` NO lo es: informa una limitación del dato. */
  is_problem: boolean;
  occurred_on: string | null;
  settlement_movement_id: string | null;
  bank_movement_id: string | null;
  transaction_ids: string[];
  settlement_amount: Money | null;
  bank_amount: Money | null;
  difference: Money | null;
  explanation: Explanation;
}

export interface FlowReport {
  contract_version: string;
  generated_at: string;
  channel_ledger_id: string;
  bank_ledger_id: string;
  coverage: Record<string, { from: string; to: string } | null>;
  counts: Record<string, number>;
  by_confidence: Record<string, number>;
  matched_amount: Money;
  unexplained_total: Money;
  /** Solo lo atribuible a los problemas. Es el número a mostrar. */
  disputed_amount: Money;
  /** Error acumulado de estimar comisiones. Un centavo por venta como máximo. */
  rounding_amount: Money;
  problem_count: number;
  findings: FlowFinding[];
}

export interface Page<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(body.detail ?? `${res.status} ${res.statusText}`, res.status);
  }
  return res.json() as Promise<T>;
}

export interface MovementFilters {
  desde?: string;
  hasta?: string;
  kind?: string;
  status?: string;
  q?: string;
  limit?: number;
  offset?: number;
}

export const api = {
  system: () => get<SystemView>("/api/system"),

  ledger: (id: string) => get<LedgerSummary>(`/api/ledgers/${id}`),

  movements: (id: string, filters: MovementFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") params.set(key, String(value));
    }
    const qs = params.toString();
    return get<Page<MovementView>>(
      `/api/ledgers/${id}/movements${qs ? `?${qs}` : ""}`,
    );
  },

  statementPeriods: (id: string) =>
    get<{ periods: string[] }>(`/api/ledgers/${id}/statements`),

  statement: (id: string, period: string) =>
    get<StatementView>(`/api/ledgers/${id}/statements/${period}`),

  transactions: (id: string) =>
    get<{
      total: number;
      con_desglose_declarado: number;
      sin_liquidar: number;
      items: TransactionBreakdown[];
    }>(`/api/ledgers/${id}/transactions`),

  flow: (canal = "wompi", banco = "bancolombia") =>
    get<FlowReport>(`/api/reconciliation/flow?canal=${canal}&banco=${banco}`),

  disbursements: (id: string) =>
    get<{
      total: number;
      cierran_en_cero: number;
      con_desglose_completo: number;
      items: DisbursementBreakdown[];
    }>(`/api/ledgers/${id}/disbursements`),
};
