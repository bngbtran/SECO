import {
  Activity,
  AlertTriangle,
  Battery,
  CheckCircle,
  Gauge,
  Info,
  Lock,
  LockOpen,
  MapPin,
  Pencil,
  Plug,
  Radio,
  RotateCcw,
  Square,
  Terminal,
  Thermometer,
  Trash2,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../../../shared/api";
import { AreaChart } from "../../../shared/charts";
import { dur, fDate, fTimeS, kwh, num, rel, shortId, vnd } from "../../../shared/format";
import { useApi, useInterval } from "../../../shared/hooks";
import { ERROR_LABEL } from "../../../shared/labels";
import { useRealtime, useRealtimeRefresh } from "../../../shared/realtime";
import type {
  AdminSessionDetail,
  AdminStationDetail,
  StationCommand,
  StationCommandResult,
  Telemetry,
} from "../../../shared/types";
import {
  AvailabilityBadge,
  Badge,
  Card,
  Dialog,
  Empty,
  ErrorBox,
  Loading,
  LockBadge,
  SessionBadge,
  SeverityBadge,
  TypeBadge,
  useToast,
} from "../../../shared/ui";
import { SessionTable, StationFormDialog } from "../components";

const COMMANDS: [StationCommand, LucideIcon, string, string][] = [
  ["LOCK", Lock, "Khoá đầu cắm", "var(--amber)"],
  ["UNLOCK", LockOpen, "Mở khoá đầu cắm", "var(--primary)"],
  ["STOP", Square, "Dừng phiên từ xa", "var(--red)"],
  ["MAINTENANCE", Wrench, "Chuyển bảo trì", "var(--amber)"],
  ["READY", CheckCircle, "Đưa về sẵn sàng", "var(--primary)"],
  ["RESET", RotateCcw, "Khởi động lại", "var(--blue)"],
  ["PING", Radio, "Kiểm tra kết nối", "var(--primary)"],
];

const COMMAND_INFO: Record<StationCommand, string> = {
  LOCK: "Khoá actuator đầu cắm. Trạng thái khoá cập nhật khi trạm gửi cmd_ack.",
  UNLOCK: "Mở khoá đầu cắm. Nếu guest còn nợ, phiên vẫn ở trạng thái chờ thanh toán.",
  STOP: "Dừng phiên đang sạc với stop_reason = TECHNICIAN. Member bị trừ ví, guest chuyển sang chờ thanh toán.",
  MAINTENANCE: "Dừng phiên đang sạc (nếu có), chuyển trạm sang MAINTENANCE và mở hồ sơ bảo trì.",
  READY: "Kết thúc bảo trì: trạm về READY, đóng hồ sơ bảo trì và đánh dấu các lỗi đang mở là đã xử lý.",
  RESET: "Khởi động lại ESP32, relay giữ OFF. Phiên đang sạc sẽ bị dừng.",
  PING: "Kiểm tra trạm còn phản hồi MQTT.",
};

function GaugeBox({
  icon: Icon,
  label,
  value,
  unit,
  pct,
  color = "var(--primary)",
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  unit: string;
  pct: number;
  color?: string;
}) {
  return (
    <div className="gauge">
      <div className="gl">
        <Icon size={15} />
        {label}
      </div>
      <div className="gv">
        {value}
        <small>{unit}</small>
      </div>
      <div className="meter">
        <i style={{ width: `${Math.min(100, Math.max(0, pct))}%`, background: color }} />
      </div>
    </div>
  );
}

function CommandDialog({
  station,
  command,
  onClose,
  onDone,
}: {
  station: AdminStationDetail;
  command: StationCommand;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api<StationCommandResult>(`/admin/stations/${station.station_id}/commands`, {
        method: "POST",
        body: { command, note: note || null },
      });
      toast(
        `Đã gửi lệnh ${command}`,
        result.published
          ? `${station.station_id}${result.stopped_session ? ` · dừng phiên ${shortId(result.stopped_session.session_id)} (${vnd(result.stopped_session.cost_vnd)})` : ""}`
          : "MQTT chưa kết nối: đã cập nhật hệ thống nhưng lệnh chưa tới trạm",
        result.published ? "ok" : "error",
      );
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={`Gửi lệnh ${command}`}
      subtitle={`${station.station_id} · ${COMMAND_INFO[command]}`}
      icon={Terminal}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-outline" onClick={onClose}>
            Huỷ
          </button>
          <button className={`btn ${["STOP", "RESET"].includes(command) ? "btn-danger" : "btn-primary"}`} onClick={send} disabled={busy}>
            {busy ? "Đang gửi…" : "Gửi lệnh"}
          </button>
        </>
      }
    >
      <div className="console" style={{ height: "auto", minHeight: 0 }}>
        → evcs/{station.station_id}/cmd {`{"command":"${command}"}`}
        <br />← chờ evcs/{station.station_id}/cmd_ack
      </div>
      {!station.is_online && (
        <div className="err" style={{ marginTop: 10 }}>
          Trạm đang OFFLINE, lệnh có thể không tới được trạm.
        </div>
      )}
      {command === "STOP" && !station.current_session && (
        <div className="hint" style={{ marginTop: 10 }}>
          Trạm hiện không có phiên đang sạc.
        </div>
      )}
      {command === "MAINTENANCE" && (
        <div className="field" style={{ marginTop: 14 }}>
          <label className="label">Ghi chú bảo trì</label>
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="VD: Kiểm tra cảm biến nhiệt" />
        </div>
      )}
      {error && (
        <div className="err" style={{ marginTop: 10 }}>
          {error}
        </div>
      )}
    </Dialog>
  );
}

/**
 * A guest is waiting to pay on this station: link to the sandbox payment page
 * (the same page the QR on the station screen opens), so the demo can pay
 * without scanning the screen.
 */
function PendingPayment({ sessionId }: { sessionId: string }) {
  const detail = useApi<AdminSessionDetail>(`/admin/sessions/${sessionId}`);
  useInterval(detail.reload, 5_000);
  const pending = detail.data?.payments.find((p) => p.status === "PENDING");
  const last = detail.data?.payments[0];

  return (
    <div
      className="card mt"
      style={{ borderColor: "var(--amber)", background: "var(--amber-50)", padding: "14px 18px" }}
    >
      <div className="between">
        <div>
          <b>Khách đang chờ thanh toán {detail.data ? vnd(detail.data.cost_vnd) : ""}</b>
          <div className="muted" style={{ fontSize: 13 }}>
            {pending
              ? `Đầu cắm đang khoá. QR hết hạn lúc ${fTimeS(pending.expires_at)}.`
              : last
                ? `QR ${last.status === "CANCELLED" ? "đã huỷ" : "đã hết hạn"}: khách bấm joystick trên trụ để tạo QR mới.`
                : "Đang tạo QR…"}
          </div>
        </div>
        {pending && (
          <a className="btn btn-primary" href={`/pay/${pending.payment_id}`} target="_blank" rel="noreferrer">
            Mở trang thanh toán (Sandbox)
          </a>
        )}
      </div>
    </div>
  );
}

export default function StationDetailPage() {
  const { stationId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { events } = useRealtime();
  const detail = useApi<AdminStationDetail>(`/admin/stations/${stationId}`);
  const [live, setLive] = useState<Telemetry>({});
  const [series, setSeries] = useState<number[]>([]);
  const [command, setCommand] = useState<StationCommand | null>(null);
  const [editing, setEditing] = useState(false);
  const sessionRef = useRef<string | null>(null);

  useRealtimeRefresh(detail.reload, (e) => e.station_id === stationId, 800);
  useInterval(detail.reload, 15_000);

  const station = detail.data;
  const session = station?.current_session ?? null;

  // Live gauge values and the energy curve of the running session come from /ws.
  useEffect(() => {
    const latest = events.find((e) => e.station_id === stationId && typeof e.voltage_v === "number");
    if (latest) {
      setLive({
        voltage_v: latest.voltage_v,
        current_a: latest.current_a,
        power_kw: latest.power_kw,
        temperature_c: latest.temperature_c,
      });
    }
    const update = events.find((e) => e.station_id === stationId && e.session_id && e.energy_kwh != null);
    if (update && update.session_id === sessionRef.current) {
      const value = num(update.energy_kwh);
      setSeries((prev) => (prev[prev.length - 1] === value ? prev : [...prev, value].slice(-240)));
    }
  }, [events, stationId]);

  useEffect(() => {
    const id = session?.session_id ?? null;
    if (id !== sessionRef.current) {
      sessionRef.current = id;
      setSeries(session ? [0, num(session.energy_kwh)] : []);
    }
  }, [session]);

  const consoleLines = useMemo(() => events.filter((e) => e.station_id === stationId).slice(0, 40), [events, stationId]);

  if (detail.error) return <ErrorBox message={detail.error} onRetry={detail.reload} />;
  if (!station) return <Loading />;

  const t: Telemetry = { ...(station.latest_telemetry ?? {}), ...live };
  const charging = station.availability === "CHARGING";
  const temp = t.temperature_c;

  const remove = async () => {
    if (!window.confirm(`Xoá trạm ${station.station_id}? Chỉ xoá được trạm chưa có lịch sử sạc.`)) return;
    try {
      await api(`/admin/stations/${station.station_id}`, { method: "DELETE" });
      toast("Đã xoá trạm", station.station_id);
      navigate("/stations");
    } catch (e) {
      toast("Không xoá được trạm", (e as Error).message, "error");
    }
  };

  return (
    <>
      <div className="ph">
        <div>
          <div className="row" style={{ gap: 12 }}>
            <h1>
              {station.station_id} · {station.name}
            </h1>
            <AvailabilityBadge value={station.availability} />
            <Badge cls={station.is_simulated ? "b-gray" : "b-ready"} dot={false}>
              {station.is_simulated ? "MOCK" : "REAL"}
            </Badge>
          </div>
          <p className="row" style={{ gap: 6 }}>
            <MapPin size={14} />
            {station.address ?? "Chưa có địa chỉ"} · heartbeat{" "}
            {station.is_simulated ? "mock" : station.last_heartbeat_at ? rel(station.last_heartbeat_at) : "chưa kết nối"}
          </p>
        </div>
        <div className="row">
          <button className="btn btn-outline" onClick={() => setEditing(true)}>
            <Pencil size={15} />
            Sửa
          </button>
          <button className="btn btn-outline" onClick={remove}>
            <Trash2 size={15} />
            Xoá
          </button>
        </div>
      </div>

      {session?.status === "WAITING_PAYMENT" && <PendingPayment sessionId={session.session_id} />}

      <div className="gauges">
        <GaugeBox icon={Zap} label="Điện áp" value={station.is_online && t.voltage_v != null ? t.voltage_v.toFixed(2) : "—"} unit="V" pct={((t.voltage_v ?? 0) / 13) * 100} />
        <GaugeBox icon={Activity} label="Dòng điện" value={charging ? num(t.current_a).toFixed(2) : "0.00"} unit="A" pct={charging ? (num(t.current_a) / 15) * 100 : 0} color="var(--blue)" />
        <GaugeBox icon={Gauge} label="Công suất" value={charging ? num(t.power_kw ?? session?.power_kw).toFixed(3) : "0.000"} unit="kW" pct={charging ? (num(t.power_kw ?? session?.power_kw) / 0.2) * 100 : 0} color="var(--blue)" />
        <GaugeBox
          icon={Thermometer}
          label="Nhiệt độ"
          value={station.is_online && temp != null ? temp.toFixed(1) : "—"}
          unit="°C"
          pct={((temp ?? 0) / 80) * 100}
          color={temp != null && temp >= 35 ? "var(--red)" : temp != null && temp > 30 ? "var(--amber)" : "var(--primary)"}
        />
      </div>

      <div className="g-2-1 mt">
        <div className="col" style={{ gap: 16 }}>
          <Card title="Điện năng phiên hiện tại" subtitle="energy_kwh tích luỹ theo tick (0.00125 kWh / 30 giây)">
            {session && session.status === "ACTIVE" ? (
              <>
                <AreaChart values={series} height={190} color="#2e6fb7" format={(x) => x.toFixed(4)} />
                <div className="between muted" style={{ fontSize: 12, marginTop: 6 }}>
                  <span>{dur(session.duration_seconds)} đang sạc</span>
                  <span className="num">
                    {kwh(session.energy_kwh, 5)} kWh × {vnd(session.price_per_kwh_vnd)} ={" "}
                    <b style={{ color: "var(--fg)" }}>{vnd(session.cost_vnd)}</b>
                  </span>
                </div>
              </>
            ) : (
              <Empty icon={Battery}>Trạm không có phiên đang sạc.</Empty>
            )}
          </Card>

          <Card
            title="Sự kiện realtime"
            subtitle={<span className="mono">/ws · station_id = {station.station_id}</span>}
            action={
              <span className="badge b-ready">
                <span className="dot" />
                Subscribed
              </span>
            }
          >
            <div className="console">
              {consoleLines.length ? (
                consoleLines.map((e, i) => (
                  <div key={`${e.receivedAt}-${i}`}>
                    <span className="tm">{fTimeS(e.receivedAt)}</span> <span className="tp">{e.type}</span>{" "}
                    <span className={e.type === "station_error" ? "ev" : ""}>
                      {JSON.stringify({ ...e, type: undefined, station_id: undefined, receivedAt: undefined })}
                    </span>
                  </div>
                ))
              ) : (
                <span className="tm">Đang chờ sự kiện của trạm…</span>
              )}
            </div>
          </Card>
        </div>

        <div className="col" style={{ gap: 16 }}>
          <Card title="Phiên hiện tại">
            {session ? (
              <>
                <div className="between" style={{ marginBottom: 12 }}>
                  <Link className="link mono" to={`/sessions/${session.session_id}`}>
                    {shortId(session.session_id)}
                  </Link>
                  <SessionBadge value={session.status} />
                </div>
                <dl className="kv">
                  <dt>Loại</dt>
                  <dd>
                    <TypeBadge value={session.type} />
                  </dd>
                  <dt>Người dùng</dt>
                  <dd>
                    {session.user_id ? (
                      <Link className="link" to={`/users/${session.user_id}`}>
                        {session.user_name}
                      </Link>
                    ) : (
                      "Khách (Guest)"
                    )}
                  </dd>
                  <dt>Bắt đầu</dt>
                  <dd>{fTimeS(session.started_at)}</dd>
                  <dt>Điện năng</dt>
                  <dd className="num">{kwh(session.energy_kwh, 5)} kWh</dd>
                  <dt>Chi phí</dt>
                  <dd className="num">{vnd(session.cost_vnd)}</dd>
                  <dt>Ngân sách</dt>
                  <dd>{session.max_budget_vnd ? vnd(session.max_budget_vnd) : "Không giới hạn"}</dd>
                </dl>
                {session.max_budget_vnd ? (
                  <div className="meter" style={{ marginTop: 12 }}>
                    <i style={{ width: `${Math.min(100, (session.cost_vnd / session.max_budget_vnd) * 100)}%` }} />
                  </div>
                ) : null}
              </>
            ) : (
              <Empty icon={Plug}>Không có phiên mở</Empty>
            )}
          </Card>

          <Card title="Điều khiển trạm" subtitle={`Gửi evcs/${station.station_id}/cmd · chờ cmd_ack`}>
            <div className="cmds">
              {COMMANDS.map(([cmd, Icon, label, color]) => (
                <button
                  key={cmd}
                  className="cmd"
                  onClick={() => setCommand(cmd)}
                  disabled={cmd === "READY" && !["MAINTENANCE", "ERROR"].includes(station.status)}
                  style={cmd === "READY" && !["MAINTENANCE", "ERROR"].includes(station.status) ? { opacity: 0.45 } : undefined}
                >
                  <span className="ci" style={{ color }}>
                    <Icon size={16} />
                  </span>
                  <div>
                    <b>{cmd}</b>
                    <span>{label}</span>
                  </div>
                </button>
              ))}
            </div>
            <div className="m-note" style={{ marginTop: 12 }}>
              <Info size={15} />
              <span>Mọi lệnh được ghi vào audit_logs. STOP ghi stop_reason = TECHNICIAN.</span>
            </div>
          </Card>

          <Card title="Phần cứng">
            <dl className="kv">
              <dt>Vi điều khiển</dt>
              <dd>ESP32-S3</dd>
              <dt>Đo V/I/P</dt>
              <dd>INA219 · 12V DC</dd>
              <dt>Nhiệt độ</dt>
              <dd>DS18B20 · ngưỡng 60°C</dd>
              <dt>Khoá đầu cắm</dt>
              <dd>
                <LockBadge value={station.connector_lock} />
              </dd>
              <dt>Tạo lúc</dt>
              <dd>{fDate(station.created_at)}</dd>
            </dl>
          </Card>
        </div>
      </div>

      <div className="g-1-1 mt">
        <Card title="Lỗi đang mở" subtitle="error_logs chưa xử lý" bodyStyle={{ padding: "12px 0 0" }}>
          {station.open_errors.length ? (
            <table className="t">
              <thead>
                <tr>
                  <th>Loại</th>
                  <th>Mức độ</th>
                  <th>Thời gian</th>
                </tr>
              </thead>
              <tbody>
                {station.open_errors.map((e) => (
                  <tr key={e.error_id}>
                    <td>{ERROR_LABEL[e.error_type] ?? e.error_type}</td>
                    <td>
                      <SeverityBadge value={e.severity} />
                    </td>
                    <td className="muted">{rel(e.occurred_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <Empty icon={AlertTriangle}>Không có lỗi đang mở</Empty>
          )}
        </Card>
        <Card title="Bảo trì" subtitle="maintenance_records gần nhất">
          {station.maintenance.length ? (
            <div className="timeline">
              {station.maintenance.map((m) => (
                <div className={`tl ${m.status === "IN_PROGRESS" ? "warn" : ""}`} key={m.maintenance_id}>
                  <b>
                    {m.technician_name ?? m.technician_id} · {m.status}
                  </b>
                  <p>{m.description}</p>
                  <p>
                    {fDate(m.started_at)}
                    {m.completed_at ? ` → ${fDate(m.completed_at)}` : " · đang thực hiện"}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <Empty icon={Wrench}>Chưa có hồ sơ bảo trì</Empty>
          )}
        </Card>
      </div>

      <Card className="mt" title="Lịch sử phiên tại trạm" subtitle="10 phiên gần nhất" bodyStyle={{ padding: "12px 0 0" }}>
        <SessionTable rows={station.recent_sessions} withStation={false} />
      </Card>

      {command && (
        <CommandDialog
          station={station}
          command={command}
          onClose={() => setCommand(null)}
          onDone={() => {
            setCommand(null);
            void detail.reload();
          }}
        />
      )}
      {editing && (
        <StationFormDialog
          station={station}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            void detail.reload();
          }}
        />
      )}
    </>
  );
}
