import {
  Car,
  CreditCard,
  Link as LinkIcon,
  Lock,
  Nfc,
  Pencil,
  Receipt,
  Trash2,
  Unlink,
  UserCheck,
  UserX,
  Wallet,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../../../shared/api";
import { fDay, initials, shortId, vnd } from "../../../shared/format";
import { useApi } from "../../../shared/hooks";
import { CARD_BADGE } from "../../../shared/labels";
import { useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminCard, AdminSession, AdminTransaction, AdminUserDetail, Page } from "../../../shared/types";
import {
  Badge,
  Dialog,
  Empty,
  ErrorBox,
  Field,
  Kpi,
  Loading,
  Pager,
  RoleBadge,
  SessionBadge,
  useToast,
} from "../../../shared/ui";
import { SessionTable, TransactionTable, UserFormDialog } from "../components";

type Tab = "cards" | "vehicles" | "sessions" | "tx" | "info";

function LinkCardDialog({ userId, onClose, onDone }: { userId: string; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const free = useApi<Page<AdminCard>>("/admin/rfid-cards", { status: "UNASSIGNED", limit: 100 });
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [cardId, setCardId] = useState("");
  const [uid, setUid] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "existing") {
        if (!cardId) throw new Error("Chọn một thẻ");
        const card = await api<AdminCard>(`/admin/rfid-cards/${cardId}/link`, { method: "PUT", body: { user_id: userId } });
        toast("Đã gán thẻ", card.uid ?? card.card_id);
      } else {
        const card = await api<AdminCard>("/admin/rfid-cards", {
          method: "POST",
          body: { uid: uid.trim() || null, user_id: userId, label: label.trim() || null },
        });
        toast(card.pending ? "Đã tạo thẻ chờ quét UID" : "Đã thêm thẻ", card.uid ?? card.card_id);
      }
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const options = free.data?.items ?? [];

  return (
    <Dialog title="Gán thẻ RFID" subtitle="Thẻ được gán sẽ dùng để bắt đầu sạc tại trạm" icon={CreditCard} onClose={onClose}>
      <form onSubmit={submit}>
        <div className="seg" style={{ marginBottom: 14 }}>
          <button type="button" className={mode === "existing" ? "on" : ""} onClick={() => setMode("existing")}>
            Thẻ chưa có chủ
          </button>
          <button type="button" className={mode === "new" ? "on" : ""} onClick={() => setMode("new")}>
            Thẻ mới
          </button>
        </div>
        {mode === "existing" ? (
          <Field label="Thẻ" hint={options.length ? undefined : "Không có thẻ nào chưa gán chủ. Chuyển sang \"Thẻ mới\"."}>
            <select className="select" value={cardId} onChange={(e) => setCardId(e.target.value)}>
              <option value="">— Chọn thẻ —</option>
              {options.map((c) => (
                <option key={c.card_id} value={c.card_id}>
                  {c.uid ?? "(chưa có UID)"} · {c.label ?? c.card_id}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <>
            <Field label="UID thẻ" hint="Để trống nếu chưa quét: thẻ ở trạng thái PENDING, chờ quét lần đầu tại trạm.">
              <input className="input mono" value={uid} onChange={(e) => setUid(e.target.value)} placeholder="VD: A1B2C3D4" />
            </Field>
            <Field label="Nhãn">
              <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="VD: Thẻ chính" />
            </Field>
          </>
        )}
        {error && <div className="err">{error}</div>}
        <div className="df" style={{ padding: "8px 0 0" }}>
          <button type="button" className="btn btn-outline" onClick={onClose}>
            Huỷ
          </button>
          <button className="btn btn-primary" disabled={busy}>
            Gán thẻ
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function UserSessions({ userId }: { userId: string }) {
  const [offset, setOffset] = useState(0);
  const data = useApi<Page<AdminSession>>("/admin/sessions", { user_id: userId, limit: 15, offset });
  if (data.error) return <ErrorBox message={data.error} onRetry={data.reload} />;
  if (!data.data) return <Loading />;
  return (
    <>
      <SessionTable rows={data.data.items} withUser={false} />
      <Pager total={data.data.total} limit={15} offset={offset} onChange={setOffset} summary={`${data.data.total} phiên`} />
    </>
  );
}

function UserTransactions({ userId }: { userId: string }) {
  const [offset, setOffset] = useState(0);
  const data = useApi<Page<AdminTransaction>>("/admin/transactions", { user_id: userId, limit: 15, offset });
  if (data.error) return <ErrorBox message={data.error} onRetry={data.reload} />;
  if (!data.data) return <Loading />;
  return (
    <>
      <TransactionTable rows={data.data.items} withUser={false} />
      <Pager total={data.data.total} limit={15} offset={offset} onChange={setOffset} summary={`${data.data.total} giao dịch`} />
    </>
  );
}

export default function UserDetailPage() {
  const { userId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const detail = useApi<AdminUserDetail>(`/admin/users/${userId}`);
  const spent = useApi<Page<AdminTransaction>>("/admin/transactions", { user_id: userId, type: "CHARGING", limit: 200 });
  const [tab, setTab] = useState<Tab>("cards");
  const [editing, setEditing] = useState(false);
  const [linking, setLinking] = useState(false);

  useRealtimeRefresh(detail.reload, (e) => e.user_id === userId && (e.event === "STOPPED" || e.event === "STARTED"));

  if (detail.error) return <ErrorBox message={detail.error} onRetry={detail.reload} />;
  const u = detail.data;
  if (!u) return <Loading />;

  const totalSpent = (spent.data?.items ?? []).reduce((a, t) => a + t.amount_vnd, 0);
  const isMember = u.role === "USER";

  const toggleActive = async () => {
    const next = !u.is_active;
    if (!next && !window.confirm(`Khoá tài khoản ${u.name}? Người dùng bị đăng xuất và thẻ RFID bị vô hiệu.`)) return;
    try {
      await api(`/admin/users/${u.user_id}`, { method: "PATCH", body: { is_active: next } });
      toast(next ? "Đã mở khoá tài khoản" : "Đã khoá tài khoản", u.name);
      void detail.reload();
    } catch (e) {
      toast("Không cập nhật được", (e as Error).message, "error");
    }
  };

  const remove = async () => {
    if (!window.confirm(`Xoá ${u.name}? Chỉ xoá được tài khoản chưa có lịch sử sạc hay giao dịch.`)) return;
    try {
      await api(`/admin/users/${u.user_id}`, { method: "DELETE" });
      toast("Đã xoá người dùng", u.name);
      navigate("/users");
    } catch (e) {
      toast("Không xoá được", (e as Error).message, "error");
    }
  };

  const unlink = async (card: AdminCard) => {
    if (!window.confirm(`Gỡ thẻ ${card.uid ?? card.card_id} khỏi ${u.name}? Thẻ sẽ bị vô hiệu.`)) return;
    try {
      await api(`/admin/rfid-cards/${card.card_id}/link`, { method: "DELETE" });
      toast("Đã gỡ link thẻ", card.uid ?? card.card_id);
      void detail.reload();
    } catch (e) {
      toast("Không gỡ được thẻ", (e as Error).message, "error");
    }
  };

  const TABS: [Tab, string][] = [
    ["cards", `Thẻ RFID (${u.cards.length})`],
    ["vehicles", `Xe (${u.vehicles.length})`],
    ["sessions", "Phiên sạc"],
    ["tx", "Giao dịch"],
    ["info", "Thông tin"],
  ];

  return (
    <>
      <div className="ph">
        <div className="row" style={{ gap: 14 }}>
          <div className="avatar lg">{initials(u.name)}</div>
          <div>
            <div className="row">
              <h1>{u.name}</h1>
              <RoleBadge value={u.role} />
              {u.is_active ? <Badge cls="b-ready">Hoạt động</Badge> : <Badge cls="b-gray">Đã khoá</Badge>}
            </div>
            <p>
              {u.email} · {u.phone ?? "chưa có SĐT"} · <span className="mono">{shortId(u.user_id)}</span>
            </p>
          </div>
        </div>
        <div className="row">
          <button className="btn btn-outline" onClick={toggleActive}>
            {u.is_active ? <UserX size={15} /> : <UserCheck size={15} />}
            {u.is_active ? "Khoá tài khoản" : "Mở khoá"}
          </button>
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

      <div className="kpis">
        <Kpi icon={Wallet} label="Số dư ví" value={isMember ? vnd(u.balance_vnd) : "—"} hint="Không bao giờ < 0" />
        <Kpi icon={Lock} label="Tiền đang hold" value="0đ" hint="wallet_holds chưa dùng trong v1" />
        <Kpi icon={Receipt} label="Tổng chi sạc" value={vnd(totalSpent)} hint={`${spent.data?.total ?? 0} phiên đã thanh toán`} />
        <Kpi
          icon={CreditCard}
          label="Phiên đang mở"
          value={u.open_session ? <SessionBadge value={u.open_session.status} /> : "Không"}
          hint={
            u.open_session ? (
              <Link className="link" to={`/sessions/${u.open_session.session_id}`}>
                {u.open_session.station_id} · {vnd(u.open_session.cost_vnd)}
              </Link>
            ) : (
              "Tối đa 1 phiên mở mỗi người"
            )
          }
        />
      </div>

      <div className="card">
        <div className="tabs-u">
          {TABS.map(([key, label]) => (
            <button key={key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
              {label}
            </button>
          ))}
        </div>

        {tab === "cards" && (
          <div className="card-b">
            <div className="between" style={{ marginBottom: 14 }}>
              <div className="muted" style={{ fontSize: 13 }}>
                Mỗi thẻ có UID duy nhất. Quét thẻ hợp lệ tại trạm là hành động bắt đầu sạc.
              </div>
              {u.role !== "ADMIN" && (
                <button className="btn btn-primary btn-sm" onClick={() => setLinking(true)} disabled={!u.is_active}>
                  <LinkIcon size={14} />
                  Gán thẻ
                </button>
              )}
            </div>
            {u.cards.length ? (
              <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))" }}>
                {u.cards.map((c) => (
                  <div className={`rfid ${c.status === "ACTIVE" ? "" : "off"}`} key={c.card_id}>
                    <div className="between">
                      <span style={{ color: "var(--primary)" }}>
                        <Nfc size={18} />
                      </span>
                      <Badge cls={CARD_BADGE[c.status]} dot={false}>
                        {c.status}
                      </Badge>
                    </div>
                    <div className="mono" style={{ fontSize: 17, fontWeight: 600, margin: "18px 0 4px", letterSpacing: ".06em" }}>
                      {c.uid ?? "Chờ quét UID"}
                    </div>
                    <div className="between muted" style={{ fontSize: 12 }}>
                      <span>
                        {c.label ?? c.card_id} · {fDay(c.created_at)}
                      </span>
                      <button className="btn btn-ghost btn-sm" onClick={() => unlink(c)} title="Gỡ link thẻ">
                        <Unlink size={14} />
                        Gỡ
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty icon={CreditCard}>{u.role === "ADMIN" ? "Tài khoản admin không dùng thẻ RFID" : "Chưa có thẻ RFID"}</Empty>
            )}
          </div>
        )}

        {tab === "vehicles" && (
          <div className="card-b">
            {u.vehicles.length ? (
              <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill,minmax(260px,1fr))" }}>
                {u.vehicles.map((v) => (
                  <div className="vehicle" key={v.vehicle_id}>
                    <span className="feed-ic" style={{ background: "var(--primary-50)", color: "var(--primary)", width: 40, height: 40 }}>
                      <Car size={18} />
                    </span>
                    <div style={{ flex: 1 }}>
                      <div className="strong mono">{v.plate_number}</div>
                      <div className="muted" style={{ fontSize: 12.5 }}>
                        {v.brand} {v.model}
                        {v.battery_kwh ? ` · pin ${v.battery_kwh} kWh` : ""}
                      </div>
                    </div>
                    {!v.is_active && <Badge cls="b-gray">Ngưng</Badge>}
                  </div>
                ))}
              </div>
            ) : (
              <Empty icon={Car}>Chưa đăng ký xe</Empty>
            )}
          </div>
        )}

        {tab === "sessions" && <UserSessions userId={u.user_id} />}
        {tab === "tx" && <UserTransactions userId={u.user_id} />}

        {tab === "info" && (
          <div className="card-b" style={{ maxWidth: 520 }}>
            <dl className="kv">
              <dt>user_id</dt>
              <dd className="mono">{u.user_id}</dd>
              <dt>Email</dt>
              <dd>{u.email}</dd>
              <dt>Điện thoại</dt>
              <dd>{u.phone ?? "—"}</dd>
              <dt>Vai trò</dt>
              <dd>{u.role}</dd>
              <dt>Ngày tạo</dt>
              <dd>{fDay(u.created_at)}</dd>
              <dt>Cập nhật</dt>
              <dd>{fDay(u.updated_at)}</dd>
              <dt>Phiên mở tối đa</dt>
              <dd>1</dd>
            </dl>
          </div>
        )}
      </div>

      {editing && (
        <UserFormDialog
          user={u}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            void detail.reload();
          }}
        />
      )}
      {linking && (
        <LinkCardDialog
          userId={u.user_id}
          onClose={() => setLinking(false)}
          onDone={() => {
            setLinking(false);
            void detail.reload();
          }}
        />
      )}
    </>
  );
}
