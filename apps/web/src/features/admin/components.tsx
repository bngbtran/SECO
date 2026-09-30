import { Receipt, SearchX, UserPlus, Zap } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../shared/api";
import { BudgetBar } from "../../shared/charts";
import { dur, fDate, kwh, num, shortId, vnd } from "../../shared/format";
import { AVAILABILITY_COLOR, STOP_LABEL, TX_LABEL } from "../../shared/labels";
import type { AdminLiveStation, AdminSession, AdminStation, AdminTransaction, AdminUser, Role } from "../../shared/types";
import { AvailabilityBadge, Badge, Dialog, Empty, Field, SessionBadge, TypeBadge, useToast } from "../../shared/ui";

// --- Tables -----------------------------------------------------------------

export function SessionTable({
  rows,
  withStation = true,
  withUser = true,
  budget = false,
}: {
  rows: AdminSession[];
  withStation?: boolean;
  withUser?: boolean;
  budget?: boolean;
}) {
  const navigate = useNavigate();
  if (!rows.length) return <Empty icon={SearchX}>Không có phiên phù hợp</Empty>;

  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>Phiên</th>
            {withStation && <th>Trạm</th>}
            <th>Loại</th>
            {withUser && <th>Người dùng</th>}
            <th>Bắt đầu</th>
            <th>Thời lượng</th>
            <th className="r">Điện năng</th>
            <th className="r">Chi phí</th>
            {budget ? <th>Ngân sách</th> : <th>Lý do dừng</th>}
            <th>Trạng thái</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.session_id} className="click" onClick={() => navigate(`/sessions/${s.session_id}`)}>
              <td className="mono strong" title={s.session_id}>
                {shortId(s.session_id)}
              </td>
              {withStation && <td>{s.station_id}</td>}
              <td>
                <TypeBadge value={s.type} />
              </td>
              {withUser && <td>{s.user_name ?? <span className="muted">Khách (Guest)</span>}</td>}
              <td className="muted">{fDate(s.started_at)}</td>
              <td className="muted">{dur(s.duration_seconds)}</td>
              <td className="r num">{kwh(s.energy_kwh)} kWh</td>
              <td className="r num strong">{vnd(s.cost_vnd)}</td>
              {budget ? (
                <td>
                  <BudgetBar cost={s.cost_vnd} budget={s.max_budget_vnd} />
                </td>
              ) : (
                <td className="muted">{s.stop_reason ? STOP_LABEL[s.stop_reason] ?? s.stop_reason : "—"}</td>
              )}
              <td>
                <SessionBadge value={s.status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Money leaving the member wallet is shown negative, money received positive. */
export function signedAmount(t: AdminTransaction) {
  return t.type === "CHARGING" ? -t.amount_vnd : t.amount_vnd;
}

export function TransactionTable({ rows, withUser = true }: { rows: AdminTransaction[]; withUser?: boolean }) {
  const navigate = useNavigate();
  if (!rows.length) return <Empty icon={Receipt}>Chưa có giao dịch</Empty>;

  return (
    <div className="table-wrap">
      <table className="t">
        <thead>
          <tr>
            <th>Giao dịch</th>
            <th>Loại</th>
            {withUser && <th>Người dùng</th>}
            <th>Phiên</th>
            <th className="r">Số tiền</th>
            <th className="r">Số dư trước</th>
            <th className="r">Số dư sau</th>
            <th>Thời gian</th>
            <th>Trạng thái</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => {
            const amount = signedAmount(t);
            return (
              <tr
                key={t.transaction_id}
                className={t.session_id ? "click" : ""}
                onClick={() => t.session_id && navigate(`/sessions/${t.session_id}`)}
              >
                <td className="mono strong" title={t.transaction_id}>
                  {shortId(t.transaction_id)}
                </td>
                <td>{TX_LABEL[t.type] ?? t.type}</td>
                {withUser && <td>{t.user_name ?? <span className="muted">Khách (Guest)</span>}</td>}
                <td className="mono muted">{shortId(t.session_id)}</td>
                <td className="r num strong" style={{ color: amount < 0 ? "var(--red)" : "var(--primary)" }}>
                  {amount > 0 ? "+" : ""}
                  {vnd(amount)}
                </td>
                <td className="r num">{t.balance_before_vnd != null ? vnd(t.balance_before_vnd) : "—"}</td>
                <td className="r num">{t.balance_after_vnd != null ? vnd(t.balance_after_vnd) : "—"}</td>
                <td className="muted">{fDate(t.created_at)}</td>
                <td>
                  <Badge cls={t.status === "COMPLETED" ? "b-ready" : "b-error"} dot={false}>
                    {t.status}
                  </Badge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// --- Station tile (dashboard / live board) ------------------------------------

export function StationTile({ station }: { station: AdminLiveStation }) {
  const navigate = useNavigate();
  const t = station.latest_telemetry ?? {};
  const s = station.current_session;
  const temp = t.temperature_c;
  const charging = station.availability === "CHARGING";

  return (
    <div className="st-tile" onClick={() => navigate(`/stations/${station.station_id}`)}>
      <span className="bar" style={{ background: AVAILABILITY_COLOR[station.availability] }} />
      <div className="between">
        <div>
          <h4>{station.station_id}</h4>
          <div className="sub">{station.name}</div>
        </div>
        <AvailabilityBadge value={station.availability} />
      </div>
      <div className="metrics">
        <div className="m">
          <span>Công suất</span>
          <b>{charging ? `${num(t.power_kw ?? s?.power_kw).toFixed(2)}kW` : "0.00kW"}</b>
        </div>
        <div className="m">
          <span>Nhiệt độ</span>
          <b style={temp != null && temp >= 60 ? { color: "var(--red)" } : undefined}>
            {station.is_online && temp != null ? `${temp.toFixed(1)}°` : "—"}
          </b>
        </div>
        <div className="m">
          <span>{s ? "Chi phí" : "Khoá"}</span>
          <b>
            {s
              ? vnd(s.cost_vnd)
              : station.connector_lock === "LOCKED"
                ? "Khoá"
                : station.connector_lock === "UNKNOWN"
                  ? "—"
                  : "Mở"}
          </b>
        </div>
      </div>
      {s && (
        <div style={{ marginTop: 9, fontSize: 11.5 }} className="muted between">
          <span>
            {shortId(s.session_id)} · {s.type}
          </span>
          <span className="num">{kwh(s.energy_kwh)} kWh</span>
        </div>
      )}
    </div>
  );
}

// --- Dialogs ------------------------------------------------------------------

export function StationFormDialog({
  station,
  onClose,
  onSaved,
}: {
  station?: AdminStation;
  onClose: () => void;
  onSaved: (station: AdminStation) => void;
}) {
  const toast = useToast();
  const editing = Boolean(station);
  const [form, setForm] = useState({
    station_id: station?.station_id ?? "",
    name: station?.name ?? "",
    address: station?.address ?? "",
    latitude: station?.latitude?.toString() ?? "",
    longitude: station?.longitude?.toString() ?? "",
    is_simulated: station?.is_simulated ?? true,
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [key]: key === "is_simulated" ? e.target.value === "MOCK" : e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body = {
      name: form.name.trim(),
      address: form.address.trim() || null,
      latitude: form.latitude ? Number(form.latitude) : null,
      longitude: form.longitude ? Number(form.longitude) : null,
      is_simulated: form.is_simulated,
    };
    try {
      const saved = editing
        ? await api<AdminStation>(`/admin/stations/${station!.station_id}`, { method: "PATCH", body })
        : await api<AdminStation>("/admin/stations", {
            method: "POST",
            body: { ...body, station_id: form.station_id.trim().toUpperCase() },
          });
      toast(editing ? "Đã cập nhật trạm" : "Đã thêm trạm", `${saved.station_id} · ${saved.name}`);
      onSaved(saved);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={editing ? "Sửa trạm sạc" : "Thêm trạm sạc"}
      subtitle="charging_stations"
      icon={Zap}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <Field label="Mã trạm (station_id)" hint={editing ? "Không đổi được sau khi tạo" : "VD: EVCS-07 · chữ in hoa, số, dấu -"}>
          <input
            className="input mono"
            value={form.station_id}
            onChange={set("station_id")}
            disabled={editing}
            required
            pattern="[A-Za-z0-9][A-Za-z0-9-]{1,31}"
          />
        </Field>
        <Field label="Tên trạm">
          <input className="input" value={form.name} onChange={set("name")} required />
        </Field>
        <Field label="Địa chỉ">
          <input className="input" value={form.address} onChange={set("address")} />
        </Field>
        <div className="g-1-1" style={{ gap: 12 }}>
          <Field label="Vĩ độ">
            <input className="input" type="number" step="any" value={form.latitude} onChange={set("latitude")} />
          </Field>
          <Field label="Kinh độ">
            <input className="input" type="number" step="any" value={form.longitude} onChange={set("longitude")} />
          </Field>
        </div>
        <Field label="Loại trạm" hint="Trạm thật (ESP32) online khi gửi heartbeat; trạm mock luôn online để hiển thị bản đồ.">
          <select className="select" value={form.is_simulated ? "MOCK" : "REAL"} onChange={set("is_simulated")}>
            <option value="REAL">REAL · ESP32 / Wokwi</option>
            <option value="MOCK">MOCK · mô phỏng</option>
          </select>
        </Field>
        {error && <div className="err">{error}</div>}
        <div className="df" style={{ padding: "8px 0 0" }}>
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Huỷ
          </button>
          <button className="btn btn-primary" disabled={busy}>
            {editing ? "Lưu" : "Thêm trạm"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export function UserFormDialog({
  user,
  onClose,
  onSaved,
}: {
  user?: AdminUser;
  onClose: () => void;
  onSaved: (user: AdminUser) => void;
}) {
  const toast = useToast();
  const editing = Boolean(user);
  const [form, setForm] = useState({
    name: user?.name ?? "",
    email: user?.email ?? "",
    phone: user?.phone ?? "",
    role: (user?.role ?? "USER") as Role,
    password: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm({ ...form, [key]: e.target.value });

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = {
      name: form.name.trim(),
      email: form.email.trim(),
      phone: form.phone.trim() || null,
      role: form.role,
    };
    if (form.password) body.password = form.password;
    try {
      const saved = editing
        ? await api<AdminUser>(`/admin/users/${user!.user_id}`, { method: "PATCH", body })
        : await api<AdminUser>("/admin/users", { method: "POST", body });
      toast(editing ? "Đã lưu thay đổi" : "Đã tạo người dùng", saved.name);
      onSaved(saved);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog title={editing ? "Sửa người dùng" : "Thêm người dùng"} subtitle="users" icon={UserPlus} onClose={onClose}>
      <form onSubmit={submit}>
        <Field label="Họ tên">
          <input className="input" value={form.name} onChange={set("name")} required />
        </Field>
        <Field label="Email">
          <input className="input" type="email" value={form.email} onChange={set("email")} required />
        </Field>
        <Field label="Số điện thoại">
          <input className="input" value={form.phone} onChange={set("phone")} />
        </Field>
        <Field label="Vai trò">
          <select className="select" value={form.role} onChange={set("role")}>
            <option value="USER">USER · Member</option>
            <option value="TECHNICIAN">TECHNICIAN · Kỹ thuật viên</option>
            <option value="ADMIN">ADMIN · Quản trị</option>
          </select>
        </Field>
        <Field
          label={editing ? "Đặt lại mật khẩu" : "Mật khẩu"}
          hint={editing ? "Để trống nếu không đổi. Đổi mật khẩu sẽ đăng xuất người dùng khỏi mọi thiết bị." : "Tối thiểu 4 ký tự"}
        >
          <input
            className="input"
            type="password"
            value={form.password}
            onChange={set("password")}
            required={!editing}
            minLength={4}
          />
        </Field>
        {error && <div className="err">{error}</div>}
        <div className="df" style={{ padding: "8px 0 0" }}>
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Huỷ
          </button>
          <button className="btn btn-primary" disabled={busy}>
            {editing ? "Lưu" : "Tạo người dùng"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}
