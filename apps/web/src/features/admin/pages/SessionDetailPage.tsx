import { Printer, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../../../shared/api";
import { AreaChart } from "../../../shared/charts";
import { dur, fDate, fTimeS, kwh, num, shortId, vnd } from "../../../shared/format";
import { useApi, useInterval } from "../../../shared/hooks";
import { STOP_LABEL } from "../../../shared/labels";
import { useRealtime, useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminSessionDetail, StationCommandResult } from "../../../shared/types";
import {
  Badge,
  Card,
  Empty,
  ErrorBox,
  Kpi,
  Loading,
  PaymentBadge,
  SessionBadge,
  TypeBadge,
  useToast,
} from "../../../shared/ui";

interface TimelineItem {
  at: string;
  title: string;
  detail: string;
  tone?: "err" | "warn";
}

function timeline(s: AdminSessionDetail): TimelineItem[] {
  const items: TimelineItem[] = [
    {
      at: s.started_at,
      title: s.type === "MEMBER" ? "Quẹt RFID · bắt đầu phiên" : "Nhấn START (Guest)",
      detail:
        s.type === "MEMBER"
          ? `${s.user_name ?? "Member"} · ngân sách ${s.max_budget_vnd ? vnd(s.max_budget_vnd) : "theo số dư ví"}`
          : `Không cần RFID · ngân sách ${s.max_budget_vnd ? vnd(s.max_budget_vnd) : "không giới hạn"}`,
    },
  ];
  if (s.ended_at) {
    items.push({
      at: s.ended_at,
      title: `Dừng sạc · ${s.stop_reason ?? ""}`,
      detail: STOP_LABEL[s.stop_reason ?? ""] ?? "",
      tone: ["E_STOP", "OVER_TEMPERATURE", "SYSTEM_ERROR"].includes(s.stop_reason ?? "") ? "err" : undefined,
    });
  }
  for (const p of s.payments) {
    items.push({ at: p.created_at, title: `Tạo QR thanh toán · ${vnd(p.amount_vnd)}`, detail: `payment ${shortId(p.payment_id)} · PENDING, đầu cắm khoá`, tone: "warn" });
    if (p.status === "EXPIRED") items.push({ at: p.expires_at, title: "QR hết hạn", detail: "Cần tạo QR mới", tone: "warn" });
    if (p.status === "CANCELLED") items.push({ at: p.expires_at, title: "QR bị huỷ", detail: "Cần tạo QR mới", tone: "warn" });
    if (p.status === "PAID" && p.paid_at) items.push({ at: p.paid_at, title: "Đã thanh toán → UNLOCK", detail: "Ghi GUEST_PAYMENT, đầu cắm mở khoá" });
  }
  for (const t of s.transactions) {
    if (t.type === "CHARGING")
      items.push({
        at: t.created_at,
        title: `Trừ ví ${vnd(t.amount_vnd)}`,
        detail: `${vnd(t.balance_before_vnd)} → ${vnd(t.balance_after_vnd)}`,
      });
  }
  if (s.status === "COMPLETED") items.push({ at: s.ended_at ?? s.started_at, title: "Phiên COMPLETED", detail: "Trạm được giải phóng" });
  return items.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
}

export default function SessionDetailPage() {
  const { sessionId = "" } = useParams();
  const toast = useToast();
  const { events } = useRealtime();
  const detail = useApi<AdminSessionDetail>(`/admin/sessions/${sessionId}`);
  const [series, setSeries] = useState<number[]>([]);
  const seeded = useRef(false);
  const s = detail.data;
  const active = s?.status === "ACTIVE";

  useRealtimeRefresh(detail.reload, (e) => e.session_id === sessionId, 800);
  useInterval(detail.reload, active ? 10_000 : null);

  useEffect(() => {
    if (s && !seeded.current) {
      seeded.current = true;
      setSeries([0, num(s.energy_kwh)]);
    }
  }, [s]);

  useEffect(() => {
    const update = events.find((e) => e.session_id === sessionId && e.energy_kwh != null);
    if (update) {
      const value = num(update.energy_kwh);
      setSeries((prev) => (prev[prev.length - 1] === value ? prev : [...prev, value]));
    }
  }, [events, sessionId]);

  if (detail.error) return <ErrorBox message={detail.error} onRetry={detail.reload} />;
  if (!s) return <Loading />;

  const stop = async () => {
    if (!window.confirm(`Dừng phiên ${shortId(s.session_id)} tại ${s.station_id}? stop_reason = TECHNICIAN.`)) return;
    try {
      const result = await api<StationCommandResult>(`/admin/stations/${s.station_id}/commands`, {
        method: "POST",
        body: { command: "STOP" },
      });
      toast("Đã dừng phiên", result.published ? s.station_id : "MQTT chưa kết nối, lệnh chưa tới trạm", result.published ? "ok" : "error");
      void detail.reload();
    } catch (e) {
      toast("Không dừng được phiên", (e as Error).message, "error");
    }
  };

  // Completed sessions: energy grows linearly with whole ticks, so a straight line is exact.
  const chart = active ? series : [0, num(s.energy_kwh)];
  const budgetPct = s.max_budget_vnd ? Math.round((s.cost_vnd / s.max_budget_vnd) * 100) : null;
  const walletTx = s.transactions.find((t) => t.type === "CHARGING");

  return (
    <>
      <div className="ph">
        <div>
          <div className="row" style={{ gap: 12 }}>
            <h1 className="mono" title={s.session_id}>
              {shortId(s.session_id)}
            </h1>
            <SessionBadge value={s.status} />
            <TypeBadge value={s.type} />
          </div>
          <p>
            <Link className="link" to={`/stations/${s.station_id}`}>
              {s.station_id}
            </Link>{" "}
            · {s.station_name} · bắt đầu {fDate(s.started_at)}
          </p>
        </div>
        <div className="row">
          {active && (
            <button className="btn btn-danger" onClick={stop}>
              <Square size={15} />
              Dừng phiên
            </button>
          )}
          <button className="btn btn-outline" onClick={() => window.print()}>
            <Printer size={15} />
            In biên lai
          </button>
        </div>
      </div>

      <div className="kpis">
        <Kpi label="Điện năng" value={kwh(s.energy_kwh, 5)} unit="kWh" hint={`${Math.round(num(s.energy_kwh) / 0.00125)} tick`} />
        <Kpi label="Chi phí" value={vnd(s.cost_vnd)} hint={`${vnd(s.price_per_kwh_vnd)}/kWh snapshot`} />
        <Kpi label="Thời lượng" value={dur(s.duration_seconds)} hint={s.ended_at ? `kết thúc ${fTimeS(s.ended_at)}` : "đang chạy"} />
        <Kpi
          label="Ngân sách"
          value={budgetPct != null ? `${budgetPct}%` : "∞"}
          hint={s.max_budget_vnd ? `${vnd(s.cost_vnd)} / ${vnd(s.max_budget_vnd)}` : "sạc tới khi rút"}
        />
      </div>

      <div className="g-2-1">
        <Card title="Điện năng tích luỹ" subtitle={active ? "energy_kwh cập nhật realtime" : "energy_kwh (Decimal128)"}>
          <AreaChart values={chart} height={220} color="#2e6fb7" format={(x) => x.toFixed(4)} />
        </Card>
        <Card title="Thông tin">
          <dl className="kv">
            <dt>Người dùng</dt>
            <dd>
              {s.user_id ? (
                <Link className="link" to={`/users/${s.user_id}`}>
                  {s.user_name}
                </Link>
              ) : (
                "Khách (Guest)"
              )}
            </dd>
            <dt>Trạm</dt>
            <dd>
              <Link className="link" to={`/stations/${s.station_id}`}>
                {s.station_id}
              </Link>
            </dd>
            <dt>Giá snapshot</dt>
            <dd className="num">{vnd(s.price_per_kwh_vnd)}/kWh</dd>
            <dt>Ngân sách tối đa</dt>
            <dd className="num">{s.max_budget_vnd ? vnd(s.max_budget_vnd) : "Không đặt"}</dd>
            <dt>Chế độ ngân sách</dt>
            <dd>{s.budget_mode ?? "—"}</dd>
            <dt>Lý do dừng</dt>
            <dd>{s.stop_reason ? <span className="mono">{s.stop_reason}</span> : "—"}</dd>
            <dt>Đầu cắm</dt>
            <dd>{s.connector_locked ? "Đang khoá" : "Mở"}</dd>
            {walletTx && (
              <>
                <dt>Số dư trước</dt>
                <dd className="num">{vnd(walletTx.balance_before_vnd)}</dd>
                <dt>Số dư sau</dt>
                <dd className="num">{vnd(walletTx.balance_after_vnd)}</dd>
              </>
            )}
            <dt>session_id</dt>
            <dd className="mono" style={{ fontSize: 11 }}>
              {s.session_id}
            </dd>
          </dl>
        </Card>
      </div>

      <div className="g-1-1 mt">
        <Card title="Dòng sự kiện">
          <div className="timeline">
            {timeline(s).map((item, i) => (
              <div className={`tl ${item.tone ?? ""}`} key={i}>
                <b>{item.title}</b>
                <p>
                  {fTimeS(item.at)} · {item.detail}
                </p>
              </div>
            ))}
          </div>
        </Card>
        <Card
          title={s.type === "GUEST" ? "Payment intents" : "Giao dịch ví"}
          subtitle={s.type === "GUEST" ? "Mỗi phiên chỉ có 1 payment PAID" : "Trừ tiền sau khi phiên kết thúc (không wallet hold)"}
          bodyStyle={{ padding: "12px 0 0" }}
        >
          {s.type === "GUEST" ? (
            s.payments.length ? (
              <table className="t">
                <thead>
                  <tr>
                    <th>Payment</th>
                    <th className="r">Số tiền</th>
                    <th>Tạo lúc</th>
                    <th>Thanh toán</th>
                    <th>Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {s.payments.map((p) => (
                    <tr key={p.payment_id}>
                      <td className="mono">{shortId(p.payment_id)}</td>
                      <td className="r num">{vnd(p.amount_vnd)}</td>
                      <td className="muted">{fTimeS(p.created_at)}</td>
                      <td className="muted">{p.paid_at ? fTimeS(p.paid_at) : "—"}</td>
                      <td>
                        <PaymentBadge value={p.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <Empty>{active ? "Payment được tạo khi guest rút sạc" : "cost = 0, không cần thanh toán"}</Empty>
            )
          ) : s.transactions.length ? (
            <table className="t">
              <thead>
                <tr>
                  <th>Giao dịch</th>
                  <th className="r">Số tiền</th>
                  <th className="r">Trước</th>
                  <th className="r">Sau</th>
                  <th>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {s.transactions.map((t) => (
                  <tr key={t.transaction_id}>
                    <td className="mono">{shortId(t.transaction_id)}</td>
                    <td className="r num" style={{ color: "var(--red)" }}>
                      -{vnd(t.amount_vnd)}
                    </td>
                    <td className="r num">{vnd(t.balance_before_vnd)}</td>
                    <td className="r num">{vnd(t.balance_after_vnd)}</td>
                    <td>
                      <Badge cls="b-ready" dot={false}>
                        {t.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty>Giao dịch được ghi khi phiên kết thúc</Empty>
          )}
        </Card>
      </div>
    </>
  );
}
