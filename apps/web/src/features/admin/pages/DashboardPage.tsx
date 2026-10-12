import {
  Activity,
  AlertTriangle,
  BatteryCharging,
  ChevronRight,
  Gauge,
  Lock,
  Plus,
  Wallet,
  WifiOff,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { AreaChart, BarChart, Donut } from "../../../shared/charts";
import { compactVnd, fTime, kwh, num, vnd } from "../../../shared/format";
import { useApi, useInterval } from "../../../shared/hooks";
import { AVAILABILITY_COLOR, AVAILABILITY_LABEL, ERROR_LABEL, STOP_LABEL } from "../../../shared/labels";
import { useRealtime, useRealtimeRefresh, type LoggedEvent } from "../../../shared/realtime";
import type { AdminDashboard, AdminLiveStation } from "../../../shared/types";
import { Card, Empty, ErrorBox, Kpi, Loading, PageHeader } from "../../../shared/ui";
import { SessionTable, StationFormDialog, StationTile } from "../components";

const AVAILABILITY_ORDER = ["AVAILABLE", "CHARGING", "WAITING_PAYMENT", "ERROR", "MAINTENANCE", "OFFLINE"];

function describe(e: LoggedEvent): { icon: LucideIcon; bg: string; fg: string; text: string } | null {
  const station = e.station_id ?? "";
  if (e.type === "station_error")
    return { icon: AlertTriangle, bg: "var(--red-50)", fg: "var(--red)", text: `${station} · ${ERROR_LABEL[e.error_type ?? ""] ?? e.error_type}` };
  if (e.type === "station_status") {
    const online = (e.data as { is_online?: boolean } | undefined)?.is_online;
    return online
      ? { icon: Activity, bg: "var(--primary-50)", fg: "var(--primary)", text: `${station} · ONLINE` }
      : { icon: WifiOff, bg: "var(--gray-50)", fg: "var(--gray)", text: `${station} · OFFLINE` };
  }
  if (e.type === "station_command_ack")
    return { icon: Lock, bg: "var(--amber-50)", fg: "var(--amber)", text: `${station} · ack ${String((e.data as { command?: string })?.command ?? "")}` };
  if (e.type === "station_updated")
    return { icon: Zap, bg: "var(--primary-50)", fg: "var(--primary)", text: `${station} · ${AVAILABILITY_LABEL[String(e.availability)] ?? e.availability}` };
  if (e.type === "member_session_updated" || e.type === "guest_session_updated") {
    const who = e.type === "guest_session_updated" ? "Guest" : "Member";
    if (e.event === "STARTED") return { icon: Zap, bg: "var(--blue-50)", fg: "var(--blue)", text: `${station} · ${who} bắt đầu sạc` };
    if (e.event === "STOPPED" || e.event === "WAITING_PAYMENT" || e.event === "COMPLETED")
      return {
        icon: BatteryCharging,
        bg: "var(--primary-50)",
        fg: "var(--primary)",
        text: `${station} · ${who} dừng · ${STOP_LABEL[e.stop_reason ?? ""] ?? e.stop_reason ?? ""} · ${vnd(e.cost_vnd)}`,
      };
    if (e.event === "PAID") return { icon: Wallet, bg: "var(--primary-50)", fg: "var(--primary)", text: `${station} · Guest đã thanh toán` };
  }
  return null;
}

function EventFeed({ fallback }: { fallback: AdminDashboard["recent_errors"] }) {
  const { events } = useRealtime();
  const items = events
    .map((e) => ({ e, d: describe(e) }))
    .filter((x) => x.d)
    .slice(0, 7);

  if (!items.length) {
    if (!fallback.length) return <Empty icon={Activity}>Chưa có sự kiện. Sự kiện mới sẽ hiện ở đây theo thời gian thực.</Empty>;
    return (
      <div className="feed">
        {fallback.map((err) => (
          <div className="feed-i" key={err.error_id}>
            <span className="feed-ic" style={{ background: "var(--red-50)", color: "var(--red)" }}>
              <AlertTriangle size={14} />
            </span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div>
                <b>{err.station_id}</b> · {ERROR_LABEL[err.error_type] ?? err.error_type}
              </div>
              <div className="t2">{err.message}</div>
            </div>
            <span className="t2">{fTime(err.occurred_at)}</span>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="feed">
      {items.map(({ e, d }, i) => {
        const Icon = d!.icon;
        return (
          <div className="feed-i" key={`${e.receivedAt}-${i}`}>
            <span className="feed-ic" style={{ background: d!.bg, color: d!.fg }}>
              <Icon size={14} />
            </span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div>{d!.text}</div>
              <div className="t2 mono">{e.type}</div>
            </div>
            <span className="t2">{fTime(e.receivedAt)}</span>
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { powerHistory } = useRealtime();
  const dashboard = useApi<AdminDashboard>("/admin/dashboard");
  const stations = useApi<AdminLiveStation[]>("/admin/monitor/stations");
  const [adding, setAdding] = useState(false);

  const refresh = () => {
    void dashboard.reload();
    void stations.reload();
  };
  useRealtimeRefresh(refresh, () => true, 1500);
  useInterval(refresh, 30_000);

  if (dashboard.error) return <ErrorBox message={dashboard.error} onRetry={dashboard.reload} />;
  if (!dashboard.data) return <Loading />;
  const d = dashboard.data;

  const segments = AVAILABILITY_ORDER.map((k) => ({ key: k, value: d.stations_by_availability[k as keyof typeof d.stations_by_availability] ?? 0, color: AVAILABILITY_COLOR[k] }));
  const online = d.stations_total - (d.stations_by_availability.OFFLINE ?? 0);
  const revenue = d.last_7_days.map((day) => day.revenue_vnd);
  const dayLabels = d.last_7_days.map((day) => day.date.slice(8, 10) + "/" + day.date.slice(5, 7));
  // Until the first 5 s sample, start from what the stations last reported.
  const snapshotPower = (stations.data ?? [])
    .filter((s) => s.availability === "CHARGING")
    .reduce((a, s) => a + num(s.latest_telemetry?.power_kw ?? s.current_session?.power_kw), 0);
  const power = powerHistory.length ? powerHistory : [snapshotPower, snapshotPower];
  const currentPower = power[power.length - 1];

  return (
    <>
      <PageHeader
        title="Tổng quan hệ thống"
        subtitle="Số liệu cập nhật realtime qua WebSocket · 1 tick = 30 giây sạc ở 0,1 kW ≈ 0.00083 kWh"
        actions={
          <button className="btn btn-primary" onClick={() => setAdding(true)}>
            <Plus size={15} />
            Thêm trạm
          </button>
        }
      />

      <div className="kpis">
        <Kpi
          icon={BatteryCharging}
          label="Phiên đang sạc"
          value={d.sessions_active}
          hint={`${d.sessions_waiting_payment} chờ thanh toán · ${d.sessions_completed_today} hoàn tất hôm nay`}
        />
        <Kpi icon={Gauge} label="Điện năng hôm nay" value={kwh(d.energy_today_kwh)} unit="kWh" hint="Các phiên đã hoàn tất · ≈ 0.00083 kWh/tick" />
        <Kpi icon={Wallet} label="Doanh thu hôm nay" value={vnd(d.revenue_today_vnd)} hint={`Giá ${vnd(d.price_per_kwh_vnd)}/kWh`} />
        <Kpi
          icon={AlertTriangle}
          iconColor={d.open_errors ? "var(--red)" : undefined}
          label="Sự cố cần xử lý"
          value={d.open_errors}
          hint={`${d.maintenance_in_progress} trạm đang bảo trì`}
        />
      </div>

      <div className="g-2-1">
        <Card
          title="Công suất toàn hệ thống"
          subtitle="Tổng P (kW) từ telemetry các trạm đang sạc"
          action={
            <span className="badge b-charging">
              <span className="dot" />
              Live
            </span>
          }
        >
          <AreaChart values={power} height={210} format={(x) => x.toFixed(2)} />
          <div className="between" style={{ fontSize: 12, color: "var(--muted-fg)", marginTop: 6 }}>
            <span>Lấy mẫu 5 giây/lần từ lúc mở trang</span>
            <span className="num">
              Hiện tại <b style={{ color: "var(--fg)" }}>{currentPower.toFixed(3)} kW</b>
            </span>
          </div>
        </Card>
        <Card title="Trạng thái trạm" subtitle={`${online}/${d.stations_total} trạm online`}>
          <div className="row" style={{ gap: 18, alignItems: "center" }}>
            <Donut segments={segments} size={140} label={d.stations_total} sub="trạm" />
            <div className="legend" style={{ flex: 1 }}>
              {segments.map((s) => (
                <div className="li" key={s.key}>
                  <span className="sw" style={{ background: s.color }} />
                  {AVAILABILITY_LABEL[s.key]}
                  <b>{s.value}</b>
                </div>
              ))}
            </div>
          </div>
        </Card>
      </div>

      <Card
        className="mt"
        title="Trạm sạc realtime"
        subtitle="Nhấn vào trạm để xem telemetry chi tiết và gửi lệnh"
        action={
          <button className="btn btn-ghost btn-sm" onClick={() => navigate("/stations")}>
            Xem tất cả
            <ChevronRight size={15} />
          </button>
        }
      >
        {stations.data ? (
          <div className="st-grid">
            {stations.data.map((station) => (
              <StationTile key={station.station_id} station={station} />
            ))}
          </div>
        ) : (
          <Loading />
        )}
      </Card>

      <div className="g-2-1 mt">
        <Card
          title="Doanh thu 7 ngày"
          subtitle="Trừ ví member + thanh toán guest, theo ngày (giờ Việt Nam)"
          action={<span className="muted num" style={{ fontSize: 13 }}>{vnd(revenue.reduce((a, b) => a + b, 0))}</span>}
        >
          <BarChart values={revenue} labels={dayLabels} format={compactVnd} highlight={6} />
        </Card>
        <Card title="Sự kiện gần đây" subtitle="evcs/+/event · status · cmd_ack" bodyStyle={{ paddingTop: 6 }}>
          <EventFeed fallback={d.recent_errors} />
        </Card>
      </div>

      <Card
        className="mt"
        title="Phiên đang hoạt động"
        subtitle="ACTIVE / WAITING_PAYMENT"
        action={
          <button className="btn btn-ghost btn-sm" onClick={() => navigate("/sessions")}>
            Tất cả phiên
            <ChevronRight size={15} />
          </button>
        }
        bodyStyle={{ padding: "12px 0 0" }}
      >
        <SessionTable rows={d.live_sessions} budget />
      </Card>

      {adding && (
        <StationFormDialog
          onClose={() => setAdding(false)}
          onSaved={(station) => {
            setAdding(false);
            navigate(`/stations/${station.station_id}`);
          }}
        />
      )}
    </>
  );
}
