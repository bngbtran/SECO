import { CheckCircle, Clock, Wrench } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { dur, fDate, fTime, shortId } from "../../../shared/format";
import { useApi, useNow } from "../../../shared/hooks";
import { useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminMaintenance, Page } from "../../../shared/types";
import { Badge, Card, Chip, Empty, ErrorBox, Kpi, Loading, PageHeader, Pager } from "../../../shared/ui";

const PAGE_SIZE = 25;

const seconds = (m: AdminMaintenance, now: number) =>
  ((m.completed_at ? new Date(m.completed_at).getTime() : now) - new Date(m.started_at).getTime()) / 1000;

export default function MaintenancePage() {
  const navigate = useNavigate();
  const now = useNow(30_000);
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const list = useApi<Page<AdminMaintenance>>("/admin/maintenance", { status, limit: PAGE_SIZE, offset });
  const inProgress = useApi<Page<AdminMaintenance>>("/admin/maintenance", { status: "IN_PROGRESS", limit: 50 });
  const completed = useApi<Page<AdminMaintenance>>("/admin/maintenance", { status: "COMPLETED", limit: 50 });

  useRealtimeRefresh(
    () => {
      void list.reload();
      void inProgress.reload();
      void completed.reload();
    },
    (e) => e.type === "station_updated",
    800,
  );

  const done = completed.data?.items ?? [];
  const avgRepair = done.length ? done.reduce((a, m) => a + seconds(m, now), 0) / done.length : null;
  const recent = [...(inProgress.data?.items ?? []), ...done]
    .sort((a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())
    .slice(0, 6);

  return (
    <>
      <PageHeader
        title="Bảo trì"
        subtitle="maintenance_records · ERROR → MAINTENANCE → sửa lỗi → READY (kỹ thuật viên: RFID + STOP 3s, hoặc admin gửi lệnh)"
      />

      <div className="kpis">
        <Kpi icon={Wrench} iconColor="var(--amber)" label="Đang bảo trì" value={inProgress.data?.total ?? "—"} hint="Trạm ở MAINTENANCE" />
        <Kpi icon={CheckCircle} label="Đã hoàn tất" value={completed.data?.total ?? "—"} hint="Hồ sơ đã đóng" />
        <Kpi
          icon={Clock}
          label="Thời gian sửa TB"
          value={avgRepair != null ? dur(avgRepair) : "—"}
          hint={done.length ? `${done.length} hồ sơ gần nhất` : "Chưa có hồ sơ hoàn tất"}
        />
        <Kpi label="Tổng hồ sơ" value={list.data && !status ? list.data.total : (inProgress.data?.total ?? 0) + (completed.data?.total ?? 0)} hint="Mọi trạng thái" />
      </div>

      <div className="g-2-1">
        <div className="card">
          <div className="toolbar">
            {[
              ["", "Tất cả"],
              ["IN_PROGRESS", "Đang thực hiện"],
              ["COMPLETED", "Hoàn tất"],
              ["CANCELLED", "Đã huỷ"],
            ].map(([k, label]) => (
              <Chip
                key={k}
                on={status === k}
                onClick={() => {
                  setStatus(k);
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
            <Empty icon={Wrench}>Chưa có hồ sơ bảo trì</Empty>
          ) : (
            <div className="table-wrap">
              <table className="t">
                <thead>
                  <tr>
                    <th>Hồ sơ</th>
                    <th>Trạm</th>
                    <th>Sự cố</th>
                    <th>Người thực hiện</th>
                    <th>Bắt đầu</th>
                    <th>Thời lượng</th>
                    <th>Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {list.data.items.map((m) => (
                    <tr key={m.maintenance_id} className="click" onClick={() => navigate(`/stations/${m.station_id}`)}>
                      <td className="mono strong">{m.maintenance_id}</td>
                      <td>{m.station_id}</td>
                      <td className="mono muted">{m.error_id ? shortId(m.error_id) : "—"}</td>
                      <td>{m.technician_name ?? m.technician_id}</td>
                      <td className="muted">{fDate(m.started_at)}</td>
                      <td className="muted">
                        {dur(seconds(m, now))}
                        {!m.completed_at && "…"}
                      </td>
                      <td>
                        <Badge cls={m.status === "COMPLETED" ? "b-ready" : m.status === "IN_PROGRESS" ? "b-maint" : "b-gray"}>
                          {m.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {list.data && <Pager total={list.data.total} limit={PAGE_SIZE} offset={offset} onChange={setOffset} summary={`${list.data.total} hồ sơ`} />}
        </div>

        <Card title="Nhật ký bảo trì">
          {recent.length ? (
            <div className="timeline">
              {recent.map((m) => (
                <div className={`tl ${m.status === "IN_PROGRESS" ? "warn" : ""}`} key={m.maintenance_id}>
                  <b>
                    {m.station_id} · {m.technician_name ?? m.technician_id}
                  </b>
                  <p>{m.description}</p>
                  <p>
                    {fDate(m.started_at)}
                    {m.completed_at ? ` → ${fTime(m.completed_at)}` : " · đang thực hiện"}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <Empty icon={Wrench}>Chưa có hoạt động bảo trì</Empty>
          )}
        </Card>
      </div>
    </>
  );
}
