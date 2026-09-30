import { AlertOctagon, AlertTriangle, CheckCircle, ExternalLink, Wrench } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../../../shared/api";
import { fDate, rel, shortId } from "../../../shared/format";
import { useApi } from "../../../shared/hooks";
import { ERROR_LABEL } from "../../../shared/labels";
import { useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminDashboard, AdminErrorLog, Page } from "../../../shared/types";
import {
  Badge,
  Chip,
  Dialog,
  Empty,
  ErrorBox,
  Kpi,
  Loading,
  PageHeader,
  Pager,
  SeverityBadge,
  useToast,
} from "../../../shared/ui";

const PAGE_SIZE = 25;

function FaultDialog({ fault, onClose, onResolved }: { fault: AdminErrorLog; onClose: () => void; onResolved: () => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  const resolve = async () => {
    setBusy(true);
    try {
      await api(`/admin/errors/${fault.error_id}/resolve`, { method: "POST" });
      toast("Đã đánh dấu đã xử lý", `${fault.station_id} · ${ERROR_LABEL[fault.error_type] ?? fault.error_type}`);
      onResolved();
    } catch (e) {
      toast("Không cập nhật được", (e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      title={ERROR_LABEL[fault.error_type] ?? fault.error_type}
      subtitle={`${fault.station_id} · ${fault.station_name ?? ""}`}
      icon={AlertTriangle}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-outline" onClick={() => navigate(`/stations/${fault.station_id}`)}>
            <ExternalLink size={15} />
            Mở trạm
          </button>
          {!fault.resolved_at && (
            <button className="btn btn-primary" onClick={resolve} disabled={busy}>
              <CheckCircle size={15} />
              Đánh dấu đã xử lý
            </button>
          )}
        </>
      }
    >
      <dl className="kv">
        <dt>Mã lỗi</dt>
        <dd className="mono">{shortId(fault.error_id)}</dd>
        <dt>Mức độ</dt>
        <dd>
          <SeverityBadge value={fault.severity} />
        </dd>
        <dt>Nội dung</dt>
        <dd>{fault.message}</dd>
        <dt>Xảy ra lúc</dt>
        <dd>{fDate(fault.occurred_at)}</dd>
        <dt>Phiên liên quan</dt>
        <dd className="mono">{fault.session_id ? shortId(fault.session_id) : "—"}</dd>
        <dt>Trạng thái</dt>
        <dd>
          {fault.resolved_at
            ? `Đã xử lý ${fDate(fault.resolved_at)}${fault.resolved_by_name ? ` · ${fault.resolved_by_name}` : ""}`
            : "Đang mở"}
        </dd>
      </dl>
      {fault.details && (
        <div className="console" style={{ height: "auto", minHeight: 0, marginTop: 14 }}>
          {JSON.stringify(fault.details, null, 2)}
        </div>
      )}
      {!fault.resolved_at && (
        <div className="m-note" style={{ marginTop: 12 }}>
          <Wrench size={15} />
          <span>Quy trình đầy đủ: gửi lệnh MAINTENANCE tại trang trạm, sửa lỗi, rồi READY. Lệnh READY tự đánh dấu các lỗi của trạm là đã xử lý.</span>
        </div>
      )}
    </Dialog>
  );
}

export default function FaultsPage() {
  const navigate = useNavigate();
  const [filter, setFilter] = useState<"OPEN" | "ALL" | "RESOLVED">("OPEN");
  const [severity, setSeverity] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<AdminErrorLog | null>(null);

  const list = useApi<Page<AdminErrorLog>>("/admin/errors", {
    resolved: filter === "ALL" ? undefined : filter === "RESOLVED",
    severity,
    limit: PAGE_SIZE,
    offset,
  });
  const open = useApi<Page<AdminErrorLog>>("/admin/errors", { resolved: false, limit: 1 });
  const critical = useApi<Page<AdminErrorLog>>("/admin/errors", { resolved: false, severity: "CRITICAL", limit: 1 });
  const resolved = useApi<Page<AdminErrorLog>>("/admin/errors", { resolved: true, limit: 1 });
  const dashboard = useApi<AdminDashboard>("/admin/dashboard");

  const reloadAll = () => {
    void list.reload();
    void open.reload();
    void critical.reload();
    void resolved.reload();
    void dashboard.reload();
  };
  useRealtimeRefresh(reloadAll, (e) => e.type === "station_error" || e.type === "station_updated", 800);

  return (
    <>
      <PageHeader title="Sự cố" subtitle="error_logs · quá nhiệt, E-Stop, lỗi cảm biến, mất kết nối" />

      <div className="kpis">
        <Kpi icon={AlertOctagon} iconColor="var(--red)" label="Đang mở" value={open.data?.total ?? "—"} hint="Chưa được xử lý" />
        <Kpi icon={AlertTriangle} iconColor="var(--red)" label="Nghiêm trọng" value={critical.data?.total ?? "—"} hint="CRITICAL đang mở" />
        <Kpi
          icon={Wrench}
          iconColor="var(--amber)"
          label="Trạm lỗi / bảo trì"
          value={dashboard.data ? `${dashboard.data.stations_by_availability.ERROR} / ${dashboard.data.stations_by_availability.MAINTENANCE}` : "—"}
          hint="ERROR / MAINTENANCE"
        />
        <Kpi icon={CheckCircle} label="Đã xử lý" value={resolved.data?.total ?? "—"} hint="Tổng số lỗi đã đóng" />
      </div>

      <div className="card">
        <div className="toolbar">
          {(
            [
              ["OPEN", "Chưa xử lý"],
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
          <div style={{ flex: 1 }} />
          <select
            className="select"
            style={{ width: 170 }}
            value={severity}
            onChange={(e) => {
              setSeverity(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">Mọi mức độ</option>
            <option value="CRITICAL">CRITICAL</option>
            <option value="ERROR">ERROR</option>
            <option value="WARNING">WARNING</option>
          </select>
        </div>

        {list.error ? (
          <ErrorBox message={list.error} onRetry={list.reload} />
        ) : !list.data ? (
          <Loading />
        ) : !list.data.items.length ? (
          <Empty icon={CheckCircle}>{filter === "OPEN" ? "Không còn sự cố nào đang mở" : "Không có sự cố phù hợp"}</Empty>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>Mã lỗi</th>
                  <th>Trạm</th>
                  <th>Loại</th>
                  <th>Mức độ</th>
                  <th>Nội dung</th>
                  <th>Thời gian</th>
                  <th>Trạng thái</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((f) => (
                  <tr key={f.error_id} className="click" onClick={() => setSelected(f)}>
                    <td className="mono strong">{shortId(f.error_id)}</td>
                    <td>
                      <button
                        className="link"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/stations/${f.station_id}`);
                        }}
                      >
                        {f.station_id}
                      </button>
                    </td>
                    <td>{ERROR_LABEL[f.error_type] ?? f.error_type}</td>
                    <td>
                      <SeverityBadge value={f.severity} />
                    </td>
                    <td className="muted" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis" }}>
                      {f.message}
                    </td>
                    <td className="muted" title={fDate(f.occurred_at)}>
                      {rel(f.occurred_at)}
                    </td>
                    <td>{f.resolved_at ? <Badge cls="b-ready">Đã xử lý</Badge> : <Badge cls="b-error">Đang mở</Badge>}</td>
                    <td className="r">
                      <button className="btn btn-ghost btn-sm">Chi tiết</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {list.data && <Pager total={list.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} summary={`${list.data.total} sự cố`} />}
      </div>

      {selected && (
        <FaultDialog
          fault={selected}
          onClose={() => setSelected(null)}
          onResolved={() => {
            setSelected(null);
            reloadAll();
          }}
        />
      )}
    </>
  );
}
