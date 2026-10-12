import { Download, Plus, Search, Zap } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { kwh, num, rel, shortId, vnd } from "../../../shared/format";
import { useApi, useInterval } from "../../../shared/hooks";
import { AVAILABILITY_LABEL } from "../../../shared/labels";
import { useRealtimeRefresh } from "../../../shared/realtime";
import type { AdminLiveStation } from "../../../shared/types";
import {
  AvailabilityBadge,
  Badge,
  Chip,
  Empty,
  ErrorBox,
  Loading,
  LockBadge,
  PageHeader,
  Pager,
  Segmented,
} from "../../../shared/ui";
import { StationFormDialog } from "../components";

const FILTERS = ["ALL", "AVAILABLE", "CHARGING", "WAITING_PAYMENT", "ERROR", "MAINTENANCE", "OFFLINE"];
const PAGE_SIZE = 20;

function exportCsv(rows: AdminLiveStation[]) {
  const header = ["station_id", "name", "address", "type", "availability", "connector_lock", "voltage_v", "power_kw", "temperature_c", "current_session_id"];
  const lines = rows.map((s) => {
    const t = s.latest_telemetry ?? {};
    return [
      s.station_id,
      s.name,
      s.address ?? "",
      s.is_simulated ? "MOCK" : "REAL",
      s.availability,
      s.connector_lock,
      t.voltage_v ?? "",
      t.power_kw ?? "",
      t.temperature_c ?? "",
      s.current_session_id ?? "",
    ]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`)
      .join(",");
  });
  const blob = new Blob(["﻿" + [header.join(","), ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `seco-stations-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

export default function StationsPage() {
  const navigate = useNavigate();
  const stations = useApi<AdminLiveStation[]>("/admin/monitor/stations");
  const [filter, setFilter] = useState("ALL");
  const [kind, setKind] = useState<"ALL" | "REAL" | "MOCK">("ALL");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [adding, setAdding] = useState(false);

  useRealtimeRefresh(stations.reload, (e) => Boolean(e.station_id), 1000);
  useInterval(stations.reload, 30_000);

  const all = stations.data ?? [];
  const searched = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return all.filter(
      (s) =>
        (kind === "ALL" || (kind === "MOCK") === s.is_simulated) &&
        (!needle || [s.station_id, s.name, s.address ?? ""].some((v) => v.toLowerCase().includes(needle))),
    );
  }, [all, q, kind]);
  const list = searched.filter((s) => filter === "ALL" || s.availability === filter);
  const count = (k: string) => (k === "ALL" ? searched.length : searched.filter((s) => s.availability === k).length);

  return (
    <>
      <PageHeader
        title="Trạm sạc"
        subtitle="Trạng thái trạm, khoá đầu cắm, telemetry và phiên hiện tại · cập nhật realtime"
        actions={
          <>
            <button className="btn btn-outline" onClick={() => exportCsv(list)} disabled={!list.length}>
              <Download size={15} />
              Xuất CSV
            </button>
            <button className="btn btn-primary" onClick={() => setAdding(true)}>
              <Plus size={15} />
              Thêm trạm
            </button>
          </>
        }
      />

      <div className="card">
        <div className="toolbar">
          <div className="input-wrap">
            <Search size={16} />
            <input
              className="input"
              placeholder="Tìm theo mã, tên, địa chỉ"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
            />
          </div>
          {FILTERS.map((k) => (
            <Chip
              key={k}
              on={filter === k}
              onClick={() => {
                setFilter(k);
                setOffset(0);
              }}
            >
              {k === "ALL" ? "Tất cả" : AVAILABILITY_LABEL[k]}
              <span className="muted">{count(k)}</span>
            </Chip>
          ))}
          <div style={{ flex: 1 }} />
          <Segmented
            value={kind}
            onChange={(v) => {
              setKind(v);
              setOffset(0);
            }}
            options={[
              ["ALL", "Tất cả"],
              ["REAL", "Trạm thật"],
              ["MOCK", "Mock"],
            ]}
          />
        </div>

        {stations.error ? (
          <ErrorBox message={stations.error} onRetry={stations.reload} />
        ) : !stations.data ? (
          <Loading />
        ) : !list.length ? (
          <Empty icon={Zap}>Không có trạm phù hợp bộ lọc</Empty>
        ) : (
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr>
                  <th>Trạm</th>
                  <th>Loại</th>
                  <th>Trạng thái</th>
                  <th>Đầu cắm</th>
                  <th className="r">Điện áp</th>
                  <th className="r">Công suất</th>
                  <th className="r">Nhiệt độ</th>
                  <th>Phiên hiện tại</th>
                </tr>
              </thead>
              <tbody>
                {list.slice(offset, offset + PAGE_SIZE).map((s) => {
                  const t = s.latest_telemetry ?? {};
                  const hot = t.temperature_c != null && t.temperature_c >= 60;
                  return (
                    <tr key={s.station_id} className="click" onClick={() => navigate(`/stations/${s.station_id}`)}>
                      <td>
                        <div className="row">
                          <span className="feed-ic" style={{ background: "var(--primary-50)", color: "var(--primary)" }}>
                            <Zap size={14} />
                          </span>
                          <div>
                            <div className="strong">
                              {s.station_id} · {s.name}
                            </div>
                            <div className="muted" style={{ fontSize: 12 }}>
                              {s.address ?? "—"}
                              {!s.is_simulated && ` · heartbeat ${s.last_heartbeat_at ? rel(s.last_heartbeat_at) : "chưa kết nối"}`}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td>
                        <Badge cls={s.is_simulated ? "b-gray" : "b-ready"} dot={false}>
                          {s.is_simulated ? "MOCK" : "REAL"}
                        </Badge>
                      </td>
                      <td>
                        <AvailabilityBadge value={s.availability} />
                      </td>
                      <td>
                        <LockBadge value={s.connector_lock} />
                      </td>
                      <td className="r num">{s.is_online && t.voltage_v != null ? `${t.voltage_v.toFixed(0)} V` : "—"}</td>
                      <td className="r num">
                        {s.availability === "CHARGING" ? `${num(t.power_kw ?? s.current_session?.power_kw).toFixed(3)} kW` : "0.000 kW"}
                      </td>
                      <td className="r num" style={hot ? { color: "var(--red)", fontWeight: 600 } : undefined}>
                        {s.is_online && t.temperature_c != null ? `${t.temperature_c.toFixed(1)}°C` : "—"}
                      </td>
                      <td>
                        {s.current_session ? (
                          <span className="num">
                            <span className="mono">{shortId(s.current_session.session_id)}</span> ·{" "}
                            {kwh(s.current_session.energy_kwh)} kWh · {vnd(s.current_session.cost_vnd)}
                          </span>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {stations.data && (
          <Pager total={list.length} limit={PAGE_SIZE} offset={offset} onChange={setOffset} summary={`${list.length} trạm`} />
        )}
      </div>

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
