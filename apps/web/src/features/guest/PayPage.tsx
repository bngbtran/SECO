import { CheckCircle2, Clock, XCircle, Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { api } from "../../shared/api";
import { dur, fTimeS, kwh, vnd } from "../../shared/format";
import { PaymentBadge } from "../../shared/ui";

/** GET /api/v1/guest/payments/{id} (GuestPaymentPageResponse). */
interface PaymentPage {
  payment_id: string;
  amount_vnd: number;
  status: "PENDING" | "PAID" | "EXPIRED" | "CANCELLED";
  provider: string;
  expires_at: string;
  paid_at: string | null;
  session_id: string;
  station_id: string;
  station_name: string | null;
  energy_kwh: string;
  duration_seconds: number;
  stop_reason: string | null;
}

const STOP_REASON: Record<string, string> = {
  UNPLUG: "Rút sạc",
  MAX_BUDGET: "Đạt ngân sách",
  TECHNICIAN: "Kỹ thuật viên / admin dừng",
  OVER_TEMPERATURE: "Quá nhiệt",
  E_STOP: "Dừng khẩn cấp",
  SYSTEM_ERROR: "Sự cố điện",
};

/**
 * Sandbox payment page opened from the station QR (UC-G08, no login).
 * Paying confirms the PaymentIntent; the API then sends UNLOCK to the station.
 */
export default function PayPage() {
  const { paymentId = "" } = useParams();
  const [page, setPage] = useState<PaymentPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(Date.now());

  const load = useCallback(async () => {
    try {
      setPage(await api<PaymentPage>(`/guest/payments/${paymentId}`));
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [paymentId]);

  useEffect(() => {
    load();
    const poll = setInterval(load, 5000);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [load]);

  const act = async (action: "pay" | "cancel") => {
    setBusy(true);
    try {
      await api(`/guest/payments/${paymentId}/${action}`, { method: "POST" });
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const left = page ? Math.max(0, Math.floor((new Date(page.expires_at).getTime() - now) / 1000)) : 0;
  const pending = page?.status === "PENDING" && left > 0;

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16, background: "var(--bg)" }}>
      <div className="card" style={{ width: "min(420px, 100%)" }}>
        <div className="card-h" style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div className="brand-mark">
            <Zap size={16} />
          </div>
          <div style={{ flex: 1 }}>
            <b>SECO · Thanh toán sạc</b>
            <div className="muted" style={{ fontSize: 12.5 }}>
              Cổng thanh toán Sandbox
            </div>
          </div>
          {page && <PaymentBadge value={page.status} />}
        </div>

        <div className="card-b">
          {!page && !error && <div className="muted">Đang tải…</div>}
          {error && !page && <div className="muted">Không tìm thấy thanh toán: {error}</div>}
          {page && (
            <>
              <div style={{ textAlign: "center", margin: "6px 0 18px" }}>
                <div className="muted" style={{ fontSize: 13 }}>
                  Số tiền cần thanh toán
                </div>
                <div style={{ fontSize: 36, fontWeight: 700, letterSpacing: "-.02em" }}>{vnd(page.amount_vnd)}</div>
              </div>
              <dl className="kv" style={{ margin: 0 }}>
                <dt>Trạm</dt>
                <dd>{page.station_name ?? page.station_id}</dd>
                <dt>Điện đã sạc</dt>
                <dd>{kwh(page.energy_kwh, 5)} kWh</dd>
                <dt>Thời gian sạc</dt>
                <dd>{dur(page.duration_seconds)}</dd>
                <dt>Lý do dừng</dt>
                <dd>{STOP_REASON[page.stop_reason ?? ""] ?? page.stop_reason ?? "—"}</dd>
                <dt>Mã thanh toán</dt>
                <dd style={{ fontFamily: "monospace", fontSize: 12 }}>{page.payment_id}</dd>
              </dl>

              {page.status === "PAID" && (
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 18, color: "var(--green)" }}>
                  <CheckCircle2 size={20} /> Đã thanh toán lúc {page.paid_at ? fTimeS(page.paid_at) : "—"}. Đầu sạc đã được
                  mở khóa, bạn có thể rút cáp.
                </div>
              )}
              {pending && (
                <div className="muted" style={{ display: "flex", gap: 6, alignItems: "center", marginTop: 18 }}>
                  <Clock size={16} /> QR còn hiệu lực {dur(left)}
                </div>
              )}
              {!pending && page.status !== "PAID" && (
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 18, color: "var(--red)" }}>
                  <XCircle size={20} /> QR {page.status === "CANCELLED" ? "đã hủy" : "đã hết hạn"}. Bấm joystick trên trụ để
                  tạo QR mới.
                </div>
              )}
              {error && <div style={{ marginTop: 12, color: "var(--red)" }}>{error}</div>}
            </>
          )}
        </div>

        {pending && (
          <div className="card-f" style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-primary btn-lg" style={{ flex: 1 }} disabled={busy} onClick={() => act("pay")}>
              Thanh toán {page ? vnd(page.amount_vnd) : ""}
            </button>
            <button className="btn btn-outline btn-lg" disabled={busy} onClick={() => act("cancel")}>
              Hủy
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
