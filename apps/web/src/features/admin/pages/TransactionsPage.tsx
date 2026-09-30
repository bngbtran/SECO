import { CreditCard, Lock, QrCode, Receipt, Wallet } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { fDate, fTime, shortId, vnd } from "../../../shared/format";
import { useApi } from "../../../shared/hooks";
import { TX_LABEL } from "../../../shared/labels";
import { useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminDashboard, AdminPayment, AdminTransaction, AdminWalletHold, Page } from "../../../shared/types";
import { Chip, Empty, ErrorBox, Kpi, Loading, PageHeader, Pager, PaymentBadge } from "../../../shared/ui";
import { TransactionTable } from "../components";

type Tab = "tx" | "payments" | "holds";
const PAGE_SIZE = 25;

function TransactionsTab() {
  const [type, setType] = useState("");
  const [offset, setOffset] = useState(0);
  const data = useApi<Page<AdminTransaction>>("/admin/transactions", { type, limit: PAGE_SIZE, offset });
  useRealtimeRefresh(data.reload, (e) => e.event === "STOPPED" || e.event === "PAID", 1500);

  return (
    <>
      <div className="toolbar">
        {["", "CHARGING", "GUEST_PAYMENT", "TOPUP"].map((k) => (
          <Chip
            key={k}
            on={type === k}
            onClick={() => {
              setType(k);
              setOffset(0);
            }}
          >
            {k ? TX_LABEL[k] : "Mọi loại"}
          </Chip>
        ))}
      </div>
      {data.error ? (
        <ErrorBox message={data.error} onRetry={data.reload} />
      ) : !data.data ? (
        <Loading />
      ) : (
        <>
          <TransactionTable rows={data.data.items} />
          <Pager total={data.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} summary={`${data.data.total} giao dịch`} />
        </>
      )}
    </>
  );
}

function PaymentsTab() {
  const navigate = useNavigate();
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const data = useApi<Page<AdminPayment>>("/admin/payments", { status, limit: PAGE_SIZE, offset });
  useRealtimeRefresh(data.reload, (e) => e.type === "guest_payment_updated" || e.type === "guest_session_updated", 1500);

  return (
    <>
      <div className="toolbar">
        {["", "PENDING", "PAID", "EXPIRED", "CANCELLED"].map((k) => (
          <Chip
            key={k}
            on={status === k}
            onClick={() => {
              setStatus(k);
              setOffset(0);
            }}
          >
            {k || "Mọi trạng thái"}
          </Chip>
        ))}
      </div>
      {data.error ? (
        <ErrorBox message={data.error} onRetry={data.reload} />
      ) : !data.data ? (
        <Loading />
      ) : !data.data.items.length ? (
        <Empty icon={QrCode}>Chưa có payment intent</Empty>
      ) : (
        <>
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>Payment</th>
                  <th>Phiên</th>
                  <th>Trạm</th>
                  <th className="r">Số tiền</th>
                  <th>Tạo lúc</th>
                  <th>Hết hạn</th>
                  <th>Thanh toán lúc</th>
                  <th>Trạng thái</th>
                </tr>
              </thead>
              <tbody>
                {data.data.items.map((p) => (
                  <tr key={p.payment_id} className="click" onClick={() => navigate(`/sessions/${p.session_id}`)}>
                    <td className="mono strong">{shortId(p.payment_id)}</td>
                    <td className="mono">{shortId(p.session_id)}</td>
                    <td>{p.station_id ?? "—"}</td>
                    <td className="r num strong">{vnd(p.amount_vnd)}</td>
                    <td className="muted">{fDate(p.created_at)}</td>
                    <td className="muted">{fTime(p.expires_at)}</td>
                    <td className="muted">{p.paid_at ? fDate(p.paid_at) : "—"}</td>
                    <td>
                      <PaymentBadge value={p.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pager total={data.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} summary={`${data.data.total} payment`} />
        </>
      )}
    </>
  );
}

function HoldsTab() {
  const [offset, setOffset] = useState(0);
  const data = useApi<Page<AdminWalletHold>>("/admin/wallet-holds", { limit: PAGE_SIZE, offset });
  if (data.error) return <ErrorBox message={data.error} onRetry={data.reload} />;
  if (!data.data) return <Loading />;
  if (!data.data.items.length)
    return (
      <Empty icon={Lock}>
        <b style={{ color: "var(--fg)" }}>Không có wallet hold</b>
        <br />
        Theo DD v1.2, hệ thống giữ cấu trúc wallet_holds nhưng chưa tạm giữ tiền; ví bị trừ sau khi phiên kết thúc.
      </Empty>
    );
  return (
    <>
      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th>Hold</th>
              <th>Người dùng</th>
              <th>Phiên</th>
              <th className="r">Số tiền</th>
              <th>Tạo lúc</th>
              <th>Trạng thái</th>
            </tr>
          </thead>
          <tbody>
            {data.data.items.map((h) => (
              <tr key={h.wallet_hold_id}>
                <td className="mono">{shortId(h.wallet_hold_id)}</td>
                <td>{h.user_name ?? h.user_id}</td>
                <td className="mono">{shortId(h.session_id)}</td>
                <td className="r num">{vnd(h.amount_vnd)}</td>
                <td className="muted">{fDate(h.created_at)}</td>
                <td>{h.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pager total={data.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} />
    </>
  );
}

export default function TransactionsPage() {
  const [tab, setTab] = useState<Tab>("tx");
  const dashboard = useApi<AdminDashboard>("/admin/dashboard");
  const pending = useApi<Page<AdminPayment>>("/admin/payments", { status: "PENDING", limit: 1 });
  const topups = useApi<Page<AdminTransaction>>("/admin/transactions", { type: "TOPUP", limit: 1 });
  const all = useApi<Page<AdminTransaction>>("/admin/transactions", { limit: 1 });
  useRealtimeRefresh(
    () => {
      void dashboard.reload();
      void pending.reload();
    },
    (e) => e.event === "STOPPED" || e.event === "PAID" || e.type === "guest_payment_updated",
    2000,
  );

  const revenue7d = (dashboard.data?.last_7_days ?? []).reduce((a, d) => a + d.revenue_vnd, 0);

  return (
    <>
      <PageHeader title="Giao dịch & thanh toán" subtitle="transactions · payment_intents (Guest Sandbox) · wallet_holds" />

      <div className="kpis">
        <Kpi icon={Wallet} label="Doanh thu 7 ngày" value={vnd(revenue7d)} hint={`Hôm nay ${vnd(dashboard.data?.revenue_today_vnd)}`} />
        <Kpi icon={Receipt} label="Tổng giao dịch" value={all.data?.total ?? "—"} hint="Trừ ví + thanh toán guest + nạp ví" />
        <Kpi
          icon={QrCode}
          iconColor={pending.data?.total ? "var(--amber)" : undefined}
          label="Payment chờ thanh toán"
          value={pending.data?.total ?? "—"}
          hint="Đầu cắm đang khoá"
        />
        <Kpi icon={CreditCard} label="Nạp ví (Demo)" value={topups.data?.total ?? "—"} hint="DEMO_TOPUP_ENABLED" />
      </div>

      <div className="card">
        <div className="tabs-u">
          {(
            [
              ["tx", `Giao dịch${all.data ? ` (${all.data.total})` : ""}`],
              ["payments", "Payment intents"],
              ["holds", "Wallet holds"],
            ] as [Tab, string][]
          ).map(([key, label]) => (
            <button key={key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
              {label}
            </button>
          ))}
        </div>
        {tab === "tx" && <TransactionsTab />}
        {tab === "payments" && <PaymentsTab />}
        {tab === "holds" && <HoldsTab />}
      </div>
    </>
  );
}
