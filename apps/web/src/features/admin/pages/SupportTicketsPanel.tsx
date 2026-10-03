import { ExternalLink, MessageSquareWarning, UserRound } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { fDate, rel, shortId } from "../../../shared/format";
import { useApi } from "../../../shared/hooks";
import { AVAILABILITY_BADGE, AVAILABILITY_LABEL, ERROR_LABEL, TICKET_STATUS_BADGE, TICKET_STATUS_LABEL } from "../../../shared/labels";
import { useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminSupportTicket, Page } from "../../../shared/types";
import { Badge, Chip, Dialog, Empty, ErrorBox, Loading, Pager, SeverityBadge } from "../../../shared/ui";

const PAGE_SIZE = 25;
type Filter = "ACTIVE" | "ALL" | "RESOLVED";

function TicketDialog({ ticket, onClose }: { ticket: AdminSupportTicket; onClose: () => void }) {
  const navigate = useNavigate();
  const snap = ticket.station_snapshot ?? {};
  const telemetry = (snap.latest_telemetry ?? {}) as Record<string, number | boolean | undefined>;
  return (
    <Dialog
      title={ticket.category_label}
      subtitle={`${ticket.station_id} · ${ticket.station_name ?? ""}`}
      icon={MessageSquareWarning}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-outline" onClick={() => navigate(`/users/${ticket.user_id}`)}>
            <UserRound size={15} />
            Mở member
          </button>
          <button className="btn btn-primary" onClick={() => navigate(`/stations/${ticket.station_id}`)}>
            <ExternalLink size={15} />
            Mở trạm
          </button>
        </>
      }
    >
      <dl className="kv">
        <dt>Mã yêu cầu</dt>
        <dd className="mono">{ticket.ticket_id}</dd>
        <dt>Trạng thái</dt>
        <dd>
          <Badge cls={TICKET_STATUS_BADGE[ticket.status]}>{TICKET_STATUS_LABEL[ticket.status]}</Badge>
        </dd>
        <dt>Member</dt>
        <dd>
          {ticket.user_name ?? "—"}
          {ticket.user_phone ? ` · ${ticket.user_phone}` : ""}
        </dd>
        <dt>Mô tả</dt>
        <dd>{ticket.description || "—"}</dd>
        <dt>Gửi lúc</dt>
        <dd>{fDate(ticket.created_at)}</dd>
        <dt>Kỹ thuật viên</dt>
        <dd>{ticket.assigned_name ? `${ticket.assigned_name} · nhận ${fDate(ticket.assigned_at!)}` : "Chưa có ai nhận"}</dd>
        {ticket.resolution_note && (
          <>
            <dt>Kết quả</dt>
            <dd>
              {ticket.resolution_note}
              {ticket.resolved_at ? ` (${fDate(ticket.resolved_at)})` : ""}
            </dd>
          </>
        )}
        <dt>Phiên liên quan</dt>
        <dd className="mono">{ticket.session_id ? shortId(ticket.session_id) : "—"}</dd>
      </dl>

      <div className="strong" style={{ margin: "16px 0 8px", fontSize: 13 }}>
        Trạm lúc member gửi
      </div>
      <dl className="kv">
        <dt>Trạng thái</dt>
        <dd>
          {snap.availability ? (
            <Badge cls={AVAILABILITY_BADGE[snap.availability] ?? "b-gray"}>{AVAILABILITY_LABEL[snap.availability] ?? snap.availability}</Badge>
          ) : (
            snap.status ?? "—"
          )}
        </dd>
        <dt>Kết nối</dt>
        <dd>{snap.is_online ? "Online" : "Offline"}</dd>
        <dt>Nhiệt độ / dòng</dt>
        <dd>
          {telemetry.temperature_c !== undefined ? `${Number(telemetry.temperature_c).toFixed(1)}°C` : "—"} /{" "}
          {telemetry.current_a !== undefined ? `${Number(telemetry.current_a).toFixed(2)} A` : "—"}
        </dd>
      </dl>
      {(snap.open_errors ?? []).length > 0 && (
        <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
          {snap.open_errors!.map((e) => (
            <div key={e.error_id} className="row" style={{ gap: 8, fontSize: 12.5 }}>
              <SeverityBadge value={e.severity} />
              <span className="strong">{ERROR_LABEL[e.error_type] ?? e.error_type}</span>
              <span className="muted">{e.message}</span>
            </div>
          ))}
        </div>
      )}
      <div className="m-note" style={{ marginTop: 12 }}>
        <MessageSquareWarning size={15} />
        <span>Kỹ thuật viên xử lý trên ứng dụng di động. Trạm thật: chỉ đóng được khi trạm đã hết lỗi (thẻ KTV + giữ STOP tại trạm). Trạm mô phỏng: KTV xác nhận đã sửa là trạm về READY.</span>
      </div>
    </Dialog>
  );
}

/** Reports sent by members from the mobile app (support_tickets). */
export default function SupportTicketsPanel({ onChanged }: { onChanged?: () => void }) {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>("ACTIVE");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<AdminSupportTicket | null>(null);
  const list = useApi<Page<AdminSupportTicket>>("/admin/support-tickets", {
    active: filter === "ACTIVE" ? true : undefined,
    status: filter === "RESOLVED" ? "RESOLVED" : undefined,
    limit: PAGE_SIZE,
    offset,
  });
  useRealtimeRefresh(
    () => {
      void list.reload();
      onChanged?.();
    },
    (e) => e.type === "support_ticket_updated",
    500,
  );

  return (
    <>
      <div className="toolbar">
        {(
          [
            ["ACTIVE", "Đang chờ / đang xử lý"],
            ["ALL", "Tất cả"],
            ["RESOLVED", "Đã xử lý"],
          ] as const
        ).map(([k, label]) => (
          <Chip
            key={k}
            on={filter === k}
            onClick={() => {
              setFilter(k);
              setOffset(0);
            }}
          >
            {label}
          </Chip>
        ))}
      </div>

      {list.error ? (
        <ErrorBox message={list.error} onRetry={list.reload} />
      ) : !list.data ? (
        <Loading />
      ) : !list.data.items.length ? (
        <Empty icon={MessageSquareWarning}>{filter === "ACTIVE" ? "Không có báo cáo nào đang mở" : "Không có báo cáo phù hợp"}</Empty>
      ) : (
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>Trạm</th>
                <th>Vấn đề</th>
                <th>Mô tả</th>
                <th>Member</th>
                <th>Kỹ thuật viên</th>
                <th>Gửi lúc</th>
                <th>Trạng thái</th>
              </tr>
            </thead>
            <tbody>
              {list.data.items.map((t) => (
                <tr key={t.ticket_id} className="click" onClick={() => setSelected(t)}>
                  <td>
                    <button
                      className="link"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigate(`/stations/${t.station_id}`);
                      }}
                    >
                      {t.station_id}
                    </button>
                  </td>
                  <td className="strong">{t.category_label}</td>
                  <td className="muted" style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {t.description || "—"}
                  </td>
                  <td>{t.user_name ?? "—"}</td>
                  <td className="muted">{t.assigned_name ?? "—"}</td>
                  <td className="muted" title={fDate(t.created_at)}>
                    {rel(t.created_at)}
                  </td>
                  <td>
                    <Badge cls={TICKET_STATUS_BADGE[t.status]}>{TICKET_STATUS_LABEL[t.status]}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {list.data && <Pager total={list.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} summary={`${list.data.total} báo cáo`} />}
      {selected && <TicketDialog ticket={selected} onClose={() => setSelected(null)} />}
    </>
  );
}
