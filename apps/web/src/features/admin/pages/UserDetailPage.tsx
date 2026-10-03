import {
  Car,
  CreditCard,
  KeyRound,
  Link as LinkIcon,
  Lock,
  LockOpen,
  Nfc,
  Pencil,
  Receipt,
  Trash2,
  RefreshCw,
  ShieldAlert,
  Unlink,
  UserCheck,
  UserX,
  Wallet,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api } from "../../../shared/api";
import { useAuth } from "../../../shared/auth";
import { fDay, initials, shortId, vnd } from "../../../shared/format";
import { useApi } from "../../../shared/hooks";
import { CARD_BADGE, CARD_BLOCK_LABEL } from "../../../shared/labels";
import { useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminCard, AdminSession, AdminTransaction, AdminUserDetail, Page } from "../../../shared/types";
import {
  Badge,
  Empty,
  ErrorBox,
  Kpi,
  Loading,
  Pager,
  RoleBadge,
  SessionBadge,
  useToast,
} from "../../../shared/ui";
import { SessionTable, TransactionTable, UserFormDialog } from "../components";
import { TemporaryPasswordDialog } from "../PasswordDialogs";
import { RegisterCardDialog } from "../RegisterCardDialog";

type Tab = "cards" | "vehicles" | "sessions" | "tx" | "info";

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
  const { user: me } = useAuth();
  const detail = useApi<AdminUserDetail>(`/admin/users/${userId}`);
  const spent = useApi<Page<AdminTransaction>>("/admin/transactions", { user_id: userId, type: "CHARGING", limit: 200 });
  const [tab, setTab] = useState<Tab>("cards");
  const [editing, setEditing] = useState(false);
  const [issuingPassword, setIssuingPassword] = useState(false);
  // null = closed; { replace: card } = swap that card; { replace: null } = add a card.
  const [scan, setScan] = useState<{ replace: AdminCard | null } | null>(null);
  const [params, setParams] = useSearchParams();

  useRealtimeRefresh(detail.reload, (e) => e.user_id === userId && (e.event === "STOPPED" || e.event === "STARTED"));

  const u = detail.data;

  // Right after "Tạo member" the page opens with ?assignCard=1: ask for the card straight away.
  useEffect(() => {
    if (u && params.get("assignCard") === "1") {
      setParams({}, { replace: true });
      if (u.role !== "ADMIN" && u.is_active) setScan({ replace: null });
    }
  }, [u, params, setParams]);

  if (detail.error) return <ErrorBox message={detail.error} onRetry={detail.reload} />;
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

  // Only the card: the account and the member's other cards keep working.
  const blockCard = async (card: AdminCard, reason: "LOCKED" | "LOST") => {
    const name = card.uid ?? card.card_id;
    const question =
      reason === "LOST"
        ? `Báo mất thẻ ${name} của ${u.name}? Thẻ bị vô hiệu vĩnh viễn; dùng "Đổi thẻ" để cấp thẻ mới.`
        : `Khoá thẻ ${name} của ${u.name}? Thẻ không quẹt sạc được cho tới khi mở khoá.`;
    if (!window.confirm(question)) return;
    try {
      await api(`/admin/rfid-cards/${card.card_id}/block`, { method: "POST", body: { reason } });
      toast(reason === "LOST" ? "Đã báo mất thẻ" : "Đã khoá thẻ", name);
      void detail.reload();
    } catch (e) {
      toast("Không khoá được thẻ", (e as Error).message, "error");
    }
  };

  const unlockCard = async (card: AdminCard) => {
    try {
      await api(`/admin/rfid-cards/${card.card_id}`, { method: "PATCH", body: { is_active: true } });
      toast("Đã mở khoá thẻ", card.uid ?? card.card_id);
      void detail.reload();
    } catch (e) {
      toast("Không mở khoá được thẻ", (e as Error).message, "error");
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
          {me?.user_id !== u.user_id && u.is_active && (
            <button className="btn btn-outline" onClick={() => setIssuingPassword(true)} title="Khi người dùng quên mật khẩu">
              <KeyRound size={15} />
              Cấp mật khẩu tạm
            </button>
          )}
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
                Quẹt thẻ trên đầu đọc tại quầy để gán. Member dùng thẻ này quét tại trạm để bắt đầu sạc.
              </div>
              {u.role !== "ADMIN" && u.cards.length > 0 && (
                <button className="btn btn-outline btn-sm" onClick={() => setScan({ replace: null })} disabled={!u.is_active}>
                  <LinkIcon size={14} />
                  Gán thêm thẻ
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
                      {c.block_reason ? (
                        <Badge cls={CARD_BADGE[c.block_reason]} dot={false}>
                          {CARD_BLOCK_LABEL[c.block_reason]}
                        </Badge>
                      ) : (
                        <Badge cls={CARD_BADGE[c.status]} dot={false}>
                          {c.status}
                        </Badge>
                      )}
                    </div>
                    <div className="mono" style={{ fontSize: 17, fontWeight: 600, margin: "18px 0 4px", letterSpacing: ".06em" }}>
                      {c.uid ?? "Chờ quét UID"}
                    </div>
                    <div className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
                      {c.label ?? c.card_id} · {fDay(c.created_at)}
                      {c.block_reason === "LOST" && " · dùng Đổi thẻ để cấp thẻ mới"}
                    </div>
                    <div className="row" style={{ gap: 2, flexWrap: "wrap", margin: "0 -6px" }}>
                      {c.status === "ACTIVE" && (
                        <>
                          <button className="btn btn-ghost btn-sm" onClick={() => blockCard(c, "LOCKED")} title="Tạm khoá thẻ">
                            <Lock size={14} />
                            Khoá
                          </button>
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() => blockCard(c, "LOST")}
                            title="Member báo mất thẻ: vô hiệu vĩnh viễn"
                          >
                            <ShieldAlert size={14} />
                            Báo mất
                          </button>
                        </>
                      )}
                      {c.status === "INACTIVE" && c.block_reason !== "LOST" && (
                        <button className="btn btn-ghost btn-sm" onClick={() => unlockCard(c)} disabled={!u.is_active}>
                          <LockOpen size={14} />
                          Mở khoá
                        </button>
                      )}
                      <button
                        className="btn btn-ghost btn-sm"
                        onClick={() => setScan({ replace: c })}
                        disabled={!u.is_active}
                        title="Quét thẻ mới thay cho thẻ này"
                      >
                        <RefreshCw size={14} />
                        Đổi thẻ
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => unlink(c)} title="Gỡ link thẻ">
                        <Unlink size={14} />
                        Gỡ
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <Empty icon={CreditCard}>
                {u.role === "ADMIN" ? (
                  "Tài khoản admin không dùng thẻ RFID"
                ) : (
                  <>
                    <div style={{ marginBottom: 12 }}>Member chưa có thẻ RFID nên chưa thể sạc bằng thẻ.</div>
                    <button className="btn btn-primary" onClick={() => setScan({ replace: null })} disabled={!u.is_active}>
                      <LinkIcon size={15} />
                      Gán thẻ
                    </button>
                  </>
                )}
              </Empty>
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
      {issuingPassword && <TemporaryPasswordDialog user={u} onClose={() => setIssuingPassword(false)} />}
      {scan && (
        <RegisterCardDialog
          userId={u.user_id}
          userName={u.name}
          replaceCard={scan.replace}
          onClose={() => setScan(null)}
          onDone={() => {
            setScan(null);
            setTab("cards");
            void detail.reload();
          }}
        />
      )}
    </>
  );
}
