import { useState } from "react";

import { kwh, num, vnd } from "../../../shared/format";
import { useApi } from "../../../shared/hooks";
import { SESSION_LABEL, STOP_LABEL } from "../../../shared/labels";
import { useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminLiveStation, AdminSession, Page } from "../../../shared/types";
import { Chip, ErrorBox, Loading, PageHeader, Pager, Segmented } from "../../../shared/ui";
import { SessionTable } from "../components";

const PAGE_SIZE = 25;

export default function SessionsPage() {
  const [status, setStatus] = useState("");
  const [type, setType] = useState<"" | "MEMBER" | "GUEST">("");
  const [stationId, setStationId] = useState("");
  const [stopReason, setStopReason] = useState("");
  const [offset, setOffset] = useState(0);
  const stations = useApi<AdminLiveStation[]>("/admin/monitor/stations");
  const sessions = useApi<Page<AdminSession>>("/admin/sessions", {
    status,
    type,
    station_id: stationId,
    stop_reason: stopReason,
    limit: PAGE_SIZE,
    offset,
  });

  useRealtimeRefresh(
    sessions.reload,
    (e) => e.type === "member_session_updated" || e.type === "guest_session_updated",
    1500,
  );

  const reset = <T,>(setter: (v: T) => void) => (value: T) => {
    setter(value);
    setOffset(0);
  };

  const rows = sessions.data?.items ?? [];
  const pageEnergy = rows.reduce((a, s) => a + num(s.energy_kwh), 0);
  const pageCost = rows.reduce((a, s) => a + s.cost_vnd, 0);

  return (
    <>
      <PageHeader title="Phiên sạc" subtitle="charging_sessions · MEMBER và GUEST, mỗi phiên snapshot giá điện lúc bắt đầu" />

      <div className="card">
        <div className="toolbar">
          {["", "ACTIVE", "WAITING_PAYMENT", "COMPLETED", "ERROR"].map((k) => (
            <Chip key={k} on={status === k} onClick={() => reset(setStatus)(k)}>
              {k ? SESSION_LABEL[k] : "Mọi trạng thái"}
            </Chip>
          ))}
          <select className="select" style={{ width: 190 }} value={stationId} onChange={(e) => reset(setStationId)(e.target.value)}>
            <option value="">Mọi trạm</option>
            {(stations.data ?? []).map((s) => (
              <option key={s.station_id} value={s.station_id}>
                {s.station_id} · {s.name}
              </option>
            ))}
          </select>
          <select className="select" style={{ width: 180 }} value={stopReason} onChange={(e) => reset(setStopReason)(e.target.value)}>
            <option value="">Mọi lý do dừng</option>
            {Object.entries(STOP_LABEL).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
          <div style={{ flex: 1 }} />
          <Segmented
            value={type}
            onChange={reset(setType)}
            options={[
              ["", "Tất cả"],
              ["MEMBER", "Member"],
              ["GUEST", "Guest"],
            ]}
          />
        </div>

        {sessions.error ? (
          <ErrorBox message={sessions.error} onRetry={sessions.reload} />
        ) : !sessions.data ? (
          <Loading />
        ) : (
          <>
            <SessionTable rows={rows} />
            <Pager
              total={sessions.data.total}
              limit={PAGE_SIZE}
              offset={offset}
              onChange={setOffset}
              summary={`${sessions.data.total} phiên · trang này ${kwh(pageEnergy)} kWh · ${vnd(pageCost)}`}
            />
          </>
        )}
      </div>
    </>
  );
}
